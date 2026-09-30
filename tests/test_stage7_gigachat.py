"""Интернет-ИИ GigaChat: секреты, провайдер, разбор ответа (ТЗ п.101).

Провайдер проверяется настоящим HTTP-обменом с локальным сервером: подмена
сети не нужна, а поведение видно целиком — заголовки, тело запроса, повторный
обмен токеном по истечении. Ключ авторизации в тестах выдуманный.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.ai import provider_result, secrets
from app.ai.connector import AIConnector, MODE_INTERNET, build_internet_provider
from app.ai.gigachat import (
    DEFAULT_SCOPE, GigaChatConfig, GigaChatProvider, TOKEN_GUARD_SECONDS,
)


# =====================================================================
# Хранилище ключа (ТЗ п.101)
# =====================================================================


def _fake_dpapi(monkeypatch):
    """Замена системного шифрования: проверяется логика, а не DPAPI."""
    monkeypatch.setattr(secrets, "dpapi_available", lambda: True)
    monkeypatch.setattr(secrets, "_dpapi_protect", lambda data: b"cipher:" + data)
    monkeypatch.setattr(
        secrets, "_dpapi_unprotect",
        lambda payload: payload[len(b"cipher:"):],
    )


def test_key_is_stored_with_protection_when_system_provides_it(
    tmp_path, monkeypatch
):
    """Windows-ключ шифруется средствами системы, а не лежит открытым."""
    store = secrets.SecretStore(path=tmp_path / "settings.json")
    _fake_dpapi(monkeypatch)

    backend = store.set(secrets.GIGACHAT_KEY, "ключ-значение")

    assert backend == secrets.BACKEND_DPAPI
    raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "ключ-значение" not in raw
    assert store.get(secrets.GIGACHAT_KEY) == "ключ-значение"
    assert store.status(secrets.GIGACHAT_KEY)["protected"]


def test_open_stored_key_is_reported_and_migrated(tmp_path, monkeypatch):
    """Открыто сохранённый ключ предупреждает и переносится в DPAPI."""
    store = secrets.SecretStore(path=tmp_path / "settings.json")
    store.set(secrets.GIGACHAT_KEY, "открытый-ключ")

    status = store.status(secrets.GIGACHAT_KEY)
    assert status["present"]
    assert not status["protected"]
    assert "открытым" in " ".join(status["warnings"])

    _fake_dpapi(monkeypatch)
    assert store.migrate_plain_to_dpapi(secrets.GIGACHAT_KEY) == ""
    assert store.backend_of(secrets.GIGACHAT_KEY) == secrets.BACKEND_DPAPI
    assert store.get(secrets.GIGACHAT_KEY) == "открытый-ключ"
    assert store.status(secrets.GIGACHAT_KEY)["protected"]


def test_environment_variable_wins_over_file(tmp_path, monkeypatch):
    """Переменная окружения используется для проверки на другой машине."""
    store = secrets.SecretStore(path=tmp_path / "settings.json")
    store.set(secrets.GIGACHAT_KEY, "из-файла")
    monkeypatch.setenv(secrets.ENV_KEY, "из-окружения")

    assert store.get(secrets.GIGACHAT_KEY) == "из-окружения"
    assert store.backend_of(secrets.GIGACHAT_KEY) == secrets.BACKEND_ENV


def test_missing_key_is_not_an_error(tmp_path, monkeypatch):
    """Нет ключа — это «не настроено», а не сбой приложения."""
    monkeypatch.delenv(secrets.ENV_KEY, raising=False)
    store = secrets.SecretStore(path=tmp_path / "settings.json")

    assert store.get(secrets.GIGACHAT_KEY) is None
    assert store.status(secrets.GIGACHAT_KEY) == {
        "present": False, "backend": None, "protected": False, "warnings": [],
    }
    store.set(secrets.GIGACHAT_KEY, "   ")
    assert store.backend_of(secrets.GIGACHAT_KEY) is None


def test_forgotten_key_is_removed_from_settings(tmp_path, monkeypatch):
    """«Забыть ключ» удаляет значение из настроек."""
    monkeypatch.delenv(secrets.ENV_KEY, raising=False)
    store = secrets.SecretStore(path=tmp_path / "settings.json")
    store.set(secrets.GIGACHAT_KEY, "ключ")

    store.forget(secrets.GIGACHAT_KEY)

    assert store.get(secrets.GIGACHAT_KEY) is None
    assert secrets.SECRETS_FILE_KEY not in json.loads(
        (tmp_path / "settings.json").read_text(encoding="utf-8")
    )


# =====================================================================
# Разбор ответа модели (ТЗ п.102, 106)
# =====================================================================


def test_model_answer_is_split_into_proposals():
    answer = (
        "1. Не приложены исполнительные схемы по разделу КЖ.\n"
        "ОСНОВАНИЕ: приказ Минстроя России № 344/пр, приложение № 3\n"
        "2. Акт испытаний сети выпущен без протокола.\n"
        "ОСНОВАНИЕ: не установлено"
    )
    proposals = provider_result.parse_proposals(answer)

    assert [p["code"] for p in proposals] == [provider_result.CODE] * 2
    assert proposals[0]["is_requirement"] is True
    assert "344/пр" in proposals[0]["basis"]
    assert "ОСНОВАНИЕ" not in proposals[0]["text"]
    assert proposals[1]["is_requirement"] is False
    assert proposals[1]["basis"] == ""
    assert "подтвердите основание" not in proposals[1]["text"]
    # Внешняя модель не предлагает изменение данных (ТЗ п.104).
    assert all(p["action"] is None for p in proposals)


def test_answer_without_findings_gives_no_proposals():
    result = provider_result.build_result(
        "Замечаний не выявлено: состав комплекта соответствует требованиям.",
        mode="INTERNET",
    )
    assert result["proposals"] == []
    assert result["status"] == provider_result.STATUS_SUCCESS
    assert result["message"]


def test_empty_answer_is_not_treated_as_success_with_findings():
    result = provider_result.build_result("", mode="INTERNET")
    assert result["proposals"] == []


# =====================================================================
# Обращение к GigaChat через локальный сервер (ТЗ п.101)
# =====================================================================


def _header(call: dict, name: str) -> str:
    """Значение заголовка: имена в HTTP нечувствительны к регистру."""
    lowered = {key.lower(): value for key, value in call["headers"].items()}
    return lowered[name.lower()]


class _Handler(BaseHTTPRequestHandler):
    """Сервер-заглушка: отдаёт токен и ответ модели."""

    calls: list = []
    token_expires_in: int = 1800
    chat_status: int = 200
    chat_body: dict = {}

    def log_message(self, *args):  # тишина в выводе тестов
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        type(self).calls.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": raw,
        })
        if self.path.endswith("/oauth"):
            self._json(200, {
                "access_token": f"token-{len(type(self).calls)}",
                "expires_in": self.token_expires_in,
                "token_type": "Bearer",
            })
            return
        if self.path.endswith("/chat/completions"):
            self._json(self.chat_status, self.chat_body)
            return
        self._json(404, {"message": "нет такого пути"})


@pytest.fixture
def giga_server(monkeypatch):
    _Handler.calls = []
    _Handler.token_expires_in = 1800
    _Handler.chat_status = 200
    _Handler.chat_body = {"choices": [{"message": {"content": "1. Проверить срок."}}]}
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setattr(
        GigaChatConfig, "__init__",
        _config_with_url(f"http://{host}:{port}/v1",
                         f"http://{host}:{port}/api/v2/oauth"),
    )
    try:
        yield _Handler
    finally:
        server.shutdown()
        server.server_close()


def _config_with_url(base_url: str, token_url: str):
    original = GigaChatConfig.__init__

    def patched(self, base_url=base_url, model=None, scope=None,
                token_url=token_url, timeout=None):
        original(
            self,
            base_url=base_url,
            model=model or "Тестовая-Модель",
            scope=scope or DEFAULT_SCOPE,
            token_url=token_url,
            timeout=timeout or 5,
        )

    return patched


def test_token_request_follows_documented_protocol(giga_server):
    """Ключ уходит в Basic, область доступа — в тело, RqUID обязателен."""
    provider = GigaChatProvider(GigaChatConfig(), "ключ-авторизации")

    token = provider.access_token()

    assert token.startswith("token-")
    call = giga_server.calls[0]
    assert call["path"].endswith("/oauth")
    assert _header(call, "Authorization") == "Basic " + base64.b64encode(
        "ключ-авторизации:".encode("utf-8")
    ).decode("ascii")
    assert _header(call, "Content-Type") == "application/x-www-form-urlencoded"
    assert _header(call, "RqUID")
    assert f"scope={DEFAULT_SCOPE}" in call["body"]


def test_chat_request_uses_bearer_token_and_model(giga_server):
    """К модели обращаются с токеном, модель и сообщения — по протоколу."""
    provider = GigaChatProvider(GigaChatConfig(), "ключ")

    text = provider.ask("Проверь комплектность.")

    assert text == "1. Проверить срок."
    chat = next(c for c in giga_server.calls if c["path"].endswith("/chat/completions"))
    assert _header(chat, "Authorization") == "Bearer token-1"
    payload = json.loads(chat["body"])
    assert payload["model"] == "Тестовая-Модель"
    assert payload["messages"][-1]["content"] == "Проверь комплектность."


def test_expired_token_is_renewed(giga_server):
    """Токен живёт около 30 минут, поэтому запрашивается заново."""
    clock = {"now": 1000.0}
    provider = GigaChatProvider(
        GigaChatConfig(), "ключ", clock=lambda: clock["now"]
    )
    provider.ask("первый")
    clock["now"] += 1800 + 1
    provider.ask("второй")

    oauth_calls = [c for c in giga_server.calls if c["path"].endswith("/oauth")]
    assert len(oauth_calls) == 2
    chat_calls = [c for c in giga_server.calls if c["path"].endswith("/chat/completions")]
    assert _header(chat_calls[-1], "Authorization").startswith("Bearer token-")


def test_token_is_not_reissued_before_it_expires(giga_server):
    """Живой токен повторно не обменивается."""
    clock = {"now": 1000.0}
    provider = GigaChatProvider(
        GigaChatConfig(), "ключ", clock=lambda: clock["now"]
    )
    provider.ask("первый")
    clock["now"] += 1800 - TOKEN_GUARD_SECONDS - 10
    provider.ask("второй")

    oauth_calls = [c for c in giga_server.calls if c["path"].endswith("/oauth")]
    assert len(oauth_calls) == 1


def test_rejected_key_is_reported_without_invented_answer(giga_server, monkeypatch):
    """Отказ службы токенов — понятная ошибка, а не правдоподобный ответ."""
    from app.ai import gigachat

    def failing(*args, **kwargs):
        raise gigachat.GigaChatError("Ключ авторизации отклонён (код 401)")

    monkeypatch.setattr(gigachat, "_token_request", failing)
    provider = GigaChatProvider(GigaChatConfig(), "неверный")
    result = provider({"request": "проверка"})

    assert result["status"] == "error"
    assert result["proposals"] == []
    assert "401" in result["message"]


def test_provider_refuses_without_key_and_model():
    """Без ключа и модели провайдер не создаётся (ТЗ п.101)."""
    provider = GigaChatProvider(GigaChatConfig(model=""), None)
    assert not provider.configured()

    result = provider({"request": "проверка"})
    assert result["status"] == "not_configured"
    assert result["proposals"] == []


def test_connector_calls_provider_in_internet_mode(giga_server):
    """Коннектор передаёт контекст провайдеру и возвращает его предложения."""
    provider = GigaChatProvider(GigaChatConfig(), "ключ")
    connector = AIConnector(mode=MODE_INTERNET, provider=provider)

    result = connector.analyze({"request": "проверка", "documents": []})

    assert result["status"] == provider_result.STATUS_SUCCESS
    assert result["proposals"][0]["code"] == provider_result.CODE
    assert any(c["path"].endswith("/chat/completions") for c in giga_server.calls)


def test_provider_is_not_built_without_configuration(monkeypatch):
    """Без настроек интернет-режим остаётся ненастроенным, а не выдуманным."""
    from app.ai import secrets as secret_store

    monkeypatch.delenv(secret_store.ENV_KEY, raising=False)
    monkeypatch.setattr(secret_store.STORE, "get", lambda name: None)
    monkeypatch.setattr(
        "app.ai.gigachat.GigaChatConfig.from_settings",
        classmethod(lambda cls: cls(model="")),
    )
    assert build_internet_provider() is None
