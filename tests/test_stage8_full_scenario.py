"""Полный тестовый сценарий ТЗ п.110.

Один тест проходит весь путь оператора — от карточки проекта до повторного
открытия программы — и проверяет, что данные сохранились на каждом шаге
(ТЗ п.110: «На каждом этапе данные должны сохраняться»).
"""

from datetime import date
from pathlib import Path

import pytest

from app.ai import normative
from app.ai.connector import AIConnector
from app.config import ARCHIVE_DIR, PACKAGES_DIR
from app.core import domain
from app.core.services import (
    ai_service, document_service, form_service, issue_service, link_service,
    project_service, storage_service,
)
from app.core.services.exporter import export_package
from app.core.services.issue_service import IssueError
from app.core.validators import check_document_dates
from app.db.database import SessionLocal, engine, init_db
from app.db.models import (
    Document, DocumentArchiveLink, DocumentLink, DocumentVersion, MaterialType,
    ProjectSection, SectionKind,
)

TEST_ACT_PAYLOAD = {
    "object_name": "Жилой корпус 2", "address": "г. Москва, ул. Стройная, 1",
    "system_type": "внутренние инженерные сети", "test_kind": "испытания",
    "system_name": "Сеть водоснабжения", "doc_date": "02.05.2024",
    "conditions": "Нормальная температура, давление 0,6 МПа",
    "instrumentation": "Манометр МП-2", "results": "Испытания пройдены",
    "deviations": "Нет", "decisions": "а) система принята",
    "participants": "Иванов И. И.\nПетров П. П.",
    "period_start": "02.05.2024", "period_end": "02.05.2024",
}

AOSR_PAYLOAD = {
    "object_name": "Корпус 2, ось А",
    "address": "г. Москва, ул. Стройная, 1",
    "work_description": "Армирование стен и установка закладных",
    "section_refs": ["КЖ-03", "КЖ-04"],
    "work_period": "с 01.04.2024 по 30.04.2024",
    "period_start": "01.04.2024", "period_end": "30.04.2024",
    "work_volume": "120 м² бетона В25",
    "has_defects": "Нет",
    "conclusion": "Работы выполнены в полном объёме, замечаний нет",
    "work_performer": "ООО «Строймонтаж»",
}


def _file(tmp_path: Path, name: str, body: bytes = b"%PDF-1.4 demo") -> Path:
    """Исходный файл оператора: он лежит вне архива (ТЗ п.72)."""
    path = tmp_path / name
    path.write_bytes(body)
    return path


def _act(db, project, doc_type, number, payload, start, end, doc_date=None):
    """Документ с заполненной формой и корректными датами (ТЗ п.42, 43)."""
    document = document_service.create_document(
        db, project.id, doc_type=doc_type, number=number,
        doc_date=doc_date or date(2024, 5, 15),
    )
    data = dict(payload)
    if doc_type != domain.DOC_TYPE_AOSR:
        data.update({
            "base_documents": "Договор №12 от 01.03.2024",
            "decisions": "Принято без замечаний",
        })
    data.update({"period_start": start, "period_end": end})
    # ТЗ п.64: решение по блоку представителя эксплуатации принимает оператор.
    form_service.save_draft(
        db, document.id, data, exploitation_choice=form_service.MISSING_OMIT_BLOCK,
    )
    db.refresh(document)
    return document


def test_full_operator_scenario_survives_restart(db, project, tmp_path):
    """ТЗ п.110: полный сценарий, включая закрытие и открытие программы."""
    # --- 1. Заполнить данные проекта (ТЗ п.30-41) ---
    project_service.update_project_card(
        db, project.id, title="Жилой корпус 2", address="г. Москва, ул. Стройная, 1",
    )
    kind = db.query(SectionKind).first()
    for code in ("КЖ-03", "КЖ-04"):
        project_service.add_section(
            db, project.id, kind_id=kind.id, code=code, name=f"Раздел {code}",
        )
    assert project_service.get_project(db, project.id).title == "Жилой корпус 2"

    # --- 2. Создать АОСР (ТЗ п.42) ---
    aosr = _act(
        db, project, domain.DOC_TYPE_AOSR, "1", AOSR_PAYLOAD,
        "01.04.2024", "30.04.2024",
    )

    # --- 3. Материал (ТЗ п.44) ---
    material_type = db.query(MaterialType).first()
    material = link_service.save_material(
        db, project.id, name="Бетон В25", material_type_id=material_type.id,
        unit="м³", quantity=120.0,
    )

    # --- 4. Сертификат качества (ТЗ п.45) ---
    certificate = storage_service.add_file_to_archive(
        db, src_path=_file(tmp_path, "Сертификат_бетон.pdf"), project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=certificate.id,
        link_role=domain.LINK_ROLE_QUALITY,
    )

    # --- 5. Исполнительная схема (ТЗ п.47) ---
    scheme = storage_service.add_file_to_archive(
        db, src_path=_file(tmp_path, "Схема_КЖ-03.pdf"), project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )

    # --- 6. Протокол (ТЗ п.48) ---
    protocol = storage_service.add_file_to_archive(
        db, src_path=_file(tmp_path, "Протокол_ультразвук.pdf"), project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_PROTOCOLS,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=protocol.id,
        link_role=domain.LINK_ROLE_PROTOCOL,
    )

    # --- 7. Проверить даты и выпустить АОСР (ТЗ п.43, 54) ---
    assert check_document_dates(db, aosr) == []
    issue_service.issue_document(db, aosr.id)
    db.refresh(aosr)
    assert aosr.status == domain.DOC_STATUS_ISSUED
    pinned_version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == aosr.id,
        DocumentVersion.issued_at.is_not(None),
    ).one()

    # --- 8. Создать акт испытаний по запросу оператора (ТЗ п.36, 49) ---
    test_act = _act(
        db, project, domain.DOC_TYPE_TEST_ACT, "1", AOSR_PAYLOAD,
        "02.05.2024", "02.05.2024", doc_date=date(2024, 5, 20),
    )
    link_service.link_material_to_test_act(db, material_id=material.id, document_id=test_act.id)
    assert link_service.list_materials_of_act(db, test_act.id)
    form_service.save_draft(db, test_act.id, TEST_ACT_PAYLOAD)
    db.refresh(test_act)
    issue_service.issue_document(db, test_act.id)

    # --- 9. Создать АООК и связать с АОСР (ТЗ п.87) ---
    aook = _act(
        db, project, domain.DOC_TYPE_AOOK, "1", AOSR_PAYLOAD,
        "01.04.2024", "30.04.2024", doc_date=date(2024, 5, 10),
    )
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    assert not check_document_dates(db, aook), "срок итогового акта должен следовать за АОСР"
    issue_service.issue_document(db, aook.id)

    # --- 10. Создать АОУСИТО (ТЗ п.34) ---
    aousito = _act(
        db, project, domain.DOC_TYPE_AOU_SITO, "1", {
            **AOSR_PAYLOAD,
            "contract_number": "Договор №12 от 01.03.2024",
            "violation_basis": "Акт освидетельствования №7 от 20.04.2024",
        },
        "01.04.2024", "30.04.2024", doc_date=date(2024, 5, 10),
    )
    link_service.link_documents(
        db, document_id=aousito.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    issue_service.issue_document(db, aousito.id)

    # --- 11. Проверить связи (ТЗ п.87-91) ---
    assert sorted(
        document.id for document in link_service.final_acts_of(db, aosr.id)
    ) == sorted([aook.id, aousito.id])
    assert link_service.finalized_acts(db, aook.id) == [aosr]
    assert db.query(DocumentArchiveLink).filter(
        DocumentArchiveLink.document_id == aosr.id
    ).count() == 3
    assert all(
        link.archive_version_id for link in db.query(DocumentArchiveLink).all()
    ), "связь должна закреплять версию файла (ТЗ п.91)"

    # --- 12. ИИ-проверка по запросу (ТЗ п.102, 105) ---
    result = ai_service.analyze(
        db, project.id, "Проверь комплект АОСР №1", AIConnector(mode="LOCAL")
    )
    assert result["answer"]["status"] == "success"
    assert result["proposals"] == [], "полный комплект замечаний не имеет"
    assert db.query(DocumentLink).count() == 2, "ИИ не создаёт связи сам (ТЗ п.104)"

    # ИИ находит настоящий пробел, но остаётся наблюдением (ТЗ п.104, 106).
    scheme_link = db.query(DocumentArchiveLink).filter(
        DocumentArchiveLink.document_id == aosr.id,
        DocumentArchiveLink.link_role == domain.LINK_ROLE_SCHEME,
    ).one()
    link_service.unlink_document_from_archive(db, scheme_link.id)
    with_gap = ai_service.analyze(
        db, project.id, "Проверь комплект АОСР №1", AIConnector(mode="LOCAL")
    )
    gap = next(
        proposal for proposal in with_gap["proposals"]
        if proposal.code == "no_scheme_linked"
    )
    assert gap.status == domain.AI_PROPOSAL_DRAFT
    assert gap.is_requirement is False and gap.action is None
    assert db.query(DocumentArchiveLink).filter(
        DocumentArchiveLink.document_id == aosr.id
    ).count() == 2, "ИИ не вернул схему сам (ТЗ п.104)"
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )

    # --- 13. Сформировать комплект и реестр (ТЗ п.69-83) ---
    folder = export_package(db, project.id, PACKAGES_DIR)
    files = {item.name for item in folder.iterdir() if item.is_file()}
    assert any(name.startswith("Реестр") for name in files), "нужен реестр выгрузки"
    assert any(name.endswith(".pdf") for name in files), "нужны PDF документов"

    # --- 14. Проверить комплект ---
    assert (folder / "Реестр_выгрузки.pdf").is_file()
    assert project_service.history_count(db, project.id) > 0

    # --- 15. Закрыть и открыть программу (ТЗ п.110) ---
    # Идентификаторы и имена файлов запоминаем до закрытия сессии: после
    # закрытия объект отсоединён, а проверять надо именно перечитанные данные.
    project_id = project.id
    aosr_id = aosr.id
    scheme_name = Path(
        storage_service.get_archive_document(db, scheme.id).versions[0].stored_path
    ).name
    pinned_version_id = pinned_version.id
    counts_before = {
        "documents": db.query(Document).count(),
        "links": db.query(DocumentLink).count(),
        "archive": db.query(DocumentArchiveLink).count(),
        "versions": db.query(DocumentVersion).count(),
        "events": project_service.history_count(db, project.id),
    }
    db.close()
    engine.dispose()
    init_db()
    reopened = SessionLocal()
    try:
        # --- 16. Открыть проект и проверить документы, связи, историю ---
        assert project_service.get_project(reopened, project_id) is not None
        assert reopened.query(ProjectSection).filter(
            ProjectSection.project_id == project_id
        ).count() == 2
        assert reopened.query(Document).count() == counts_before["documents"]
        assert reopened.query(DocumentLink).count() == counts_before["links"]
        assert reopened.query(DocumentArchiveLink).count() == counts_before["archive"]
        assert reopened.query(DocumentVersion).count() == counts_before["versions"]
        assert project_service.history_count(reopened, project_id) == counts_before["events"]

        restored = reopened.get(Document, aosr_id)
        assert restored.status == domain.DOC_STATUS_ISSUED
        version = reopened.get(DocumentVersion, pinned_version_id)
        assert version.payload["object_name"] == "Корпус 2, ось А"
        assert version.issued_at is not None

        # --- 17. Выданный документ не меняется (ТЗ п.85) ---
        with pytest.raises(IssueError):
            issue_service.issue_document(reopened, aosr_id)

        # --- 18. Архивные файлы на месте и целы (ТЗ п.49, 72) ---
        assert (ARCHIVE_DIR / scheme_name).is_file(), "файл схемы должен остаться на диске"
        assert storage_service.find_orphan_files(reopened) == []

        # --- 19. История читается после перезапуска (ТЗ п.86) ---
        events = project_service.list_events(reopened, project_id)
        assert events
        assert any(
            event.event_type == domain.HISTORY_AI_PROPOSED for event in events
        ), "предложения ИИ остаются в истории после перезапуска"
    finally:
        reopened.close()


def test_full_scenario_normative_basis_is_visible(db, project):
    """ТЗ п.106: нормативный ответ ИИ сопровождается основанием."""
    document = _act(
        db, project, domain.DOC_TYPE_AOSR, "1", AOSR_PAYLOAD,
        "01.04.2024", "30.04.2024",
    )
    issue_service.issue_document(db, document.id)
    _act(
        db, project, domain.DOC_TYPE_AOOK, "1", AOSR_PAYLOAD,
        "30.04.2024", "30.04.2024",
    )

    result = ai_service.analyze(
        db, project.id, "Проверь комплект АОСР №1", AIConnector(mode="LOCAL")
    )
    normative_proposals = [
        proposal for proposal in result["proposals"] if proposal.is_requirement
    ]
    assert normative_proposals, "должно быть хотя бы одно требование с основанием"
    for proposal in normative_proposals:
        assert proposal.basis["document"]
        assert proposal.basis["section"]
        assert normative.format_basis(proposal.basis) != "основание не указано"
