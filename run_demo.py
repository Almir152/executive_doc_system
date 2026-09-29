from pathlib import Path
from datetime import datetime
from app.config import BASE_DIR
from app.db.database import init_db, SessionLocal
from app.db.models import Project, Document
from app.core.services.storage_service import add_file_to_archive, link_file_to_document
from app.core.services.exporter import export_package
from app.ai.connector import AIConnector

def main():
    print("=== ЗАПУСК СКВОЗНОЙ ПРОВЕРКИ СИСТЕМЫ (ТЗ Раздел 110) ===")
    
    # 1. Инициализация базы данных
    init_db()
    db = SessionLocal()

    # 2. Создание проекта
    project = Project(
        direction="Общестроительные работы",
        title="Строительство корпуса МФТИ",
        address="г. Долгопрудный, ул. Первомайская"
    )
    db.add(project)
    db.commit()
    print(f"[+] Проект создан: '{project.title}' (ID: {project.id})")

    # 3. Создание актов АОСР
    aosr1 = Document(project_id=project.id, doc_type="АОСР", number="1")
    aosr2 = Document(project_id=project.id, doc_type="АОСР", number="2")
    db.add_all([aosr1, aosr2])
    db.commit()
    print(f"[+] Добавлены акты: АОСР №1 и АОСР №2")

    # 4. Имитация загрузки исполнительной схемы (PDF)
    demo_scheme_path = BASE_DIR / "storage" / "temp_scheme.pdf"
    demo_scheme_path.write_bytes(b"%PDF-1.4 Demo Executive Scheme Content")

    # Добавляем файл в архив и связываем с двумя актами
    archive_file = add_file_to_archive(db, demo_scheme_path, "Исполнительная схема")
    link_file_to_document(db, aosr1.id, archive_file.id)
    link_file_to_document(db, aosr2.id, archive_file.id)
    db.refresh(archive_file)
    
    print(f"[+] Файл схемы сохранен в архив: {archive_file.original_name}")
    print(f"[+] Проверка дедупликации и связей: Файл прикреплен к {archive_file.links_count} актам")

    # 5. Проверка через AI Connector (Раздел 6 ТЗ)
    ai = AIConnector(mode="LOCAL")
    analysis = ai.analyze_package([aosr1.id, aosr2.id])
    print(f"[+] AI Connector статус: {analysis['status']}")
    print(f"    Предложение ИИ: {analysis['proposals'][0]}")

    # 6. Формирование выгрузки комплекта (Раздел 69-81 ТЗ)
    package_dir = BASE_DIR / "storage" / "packages" / "Комплект_01"
    export_package(db, project.id, package_dir)
    print(f"[+] Комплект успешно сформирован в папке: {package_dir}")
    
    # Проверяем созданные файлы
    generated_files = list(package_dir.glob("*.pdf"))
    print(f"[+] Сгенерировано PDF-файлов в комплекте: {len(generated_files)}")
    for f in generated_files:
        print(f"    - {f.name}")

    # Убираем временный файл
    if demo_scheme_path.exists():
        demo_scheme_path.unlink()

    db.close()
    print("\n=== ПРОВЕРКА УСПЕШНО ЗАВЕРШЕНА ===")

if __name__ == "__main__":
    main()
