"""Провайдер GigaChat для интернет-режима ИИ (ТЗ п.101).

Модуль ничего не знает о проекте: он получает готовый контекст и возвращает
предложения в формате коннектора. Доступ к внешней сети — единственное, что
он делает, и только по явному выбору режима оператором (ТЗ п.9).

Протокол взят из документации GigaChat:

* ``POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth`` — обмен
  ключа авторизации на краткоживущий access token (около 30 минут).
  Заголовок ``Authorization: Basic <ключ>``, тело ``application/x-www-form-
  urlencoded`` с ``scope``, ``RqUID`` — идентификатор запроса;
* ``POST {base_url}/chat/completions`` — обращение к модели с заголовком
  ``Authorization: Bearer <access token>``.

Ключ авторизации приложение не хранит: его выдаёт хранилище секретов.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import ssl
import time
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.giga.chat/v1"
DEFAULT_MODEL = "GigaChat-Max"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
#: Запас до истечения токена, чтобы не опрашивать истёкший (около 30 минут).
TOKEN_GUARD_SECONDS = 60
DEFAULT_TIMEOUT = 60
#: Файл с корпоративным корнем сертификации: на сетях с перехватом TLS
#: системных сертификатов недостаточно, корнем нужно доверять явно.
CA_BUNDLE_ENV = "EXECUTIVE_DOC_CA_BUNDLE"

#: Коды предложений: модель не выдумывает существующие коды (ТЗ п.106).
CODE_GIGA_REVIEW = "gigachat_review"

SYSTEM_PROMPT = (
    "Ты помощник специалиста технического надзора в строительстве. "
    "Тебе переданы сведения о проекте, его документах и нормативной базе. "
    "Отвечай по-русски, кратко и по существу. Не выдумывай реквизиты документов, "
    "номера актов и требования нормативных актов: если данных не хватает, "
    "скажи об этом прямо. Каждое замечание завершай строкой вида "
    "ОСНОВАНИЕ: <документ> <пункт> либо ОСНОВАНИЕ: не установлено. "
    "Предложи только действия, которые оператор может выполнить вручную."
)


class GigaChatError(RuntimeError):
    """Провайдер недоступен или отказал: это не повод придумывать ответ."""


class GigaChatConfig:
    """Параметры обращения к GigaChat из настроек оператора."""

    def __init__(self, base_url=DEFAULT_BASE_URL, model=DEFAULT_MODEL,
                 scope=DEFAULT_SCOPE, token_url=DEFAULT_TOKEN_URL,
                 timeout=DEFAULT_TIMEOUT):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.scope = scope or DEFAULT_SCOPE
        self.token_url = token_url or DEFAULT_TOKEN_URL
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "GigaChatConfig":
        from app import settings

        return cls(
            base_url=settings.get_setting("gigachat_base_url", DEFAULT_BASE_URL),
            model=settings.get_setting("gigachat_model", DEFAULT_MODEL),
            scope=settings.get_setting("gigachat_scope", DEFAULT_SCOPE),
        )


def ssl_context() -> ssl.SSLContext:
    """Контекст TLS: системные сертификаты плюс корпоративный корень.

    Проверку сертификата отключать нельзя: по этому соединению уходит ключ
    авторизации. Если сертификат не проходит проверку, значит сертификат
    выпущен не для GigaChat — доверять такому соединению нельзя.
    """
    context = ssl.create_default_context()
    bundle = (os.environ.get(CA_BUNDLE_ENV) or "").strip()
    if not bundle:
        return context
    if not os.path.isfile(bundle):
        raise GigaChatError(
            f"Файл сертификатов из {CA_BUNDLE_ENV} не найден: {bundle}"
        )
    try:
        context.load_verify_locations(cafile=bundle)
    except (OSError, ssl.SSLError) as exc:
        raise GigaChatError(
            f"Не удалось прочитать сертификаты из {CA_BUNDLE_ENV} ({bundle}): {exc}"
        ) from exc
    return context


def _ssl_problem(exc: ssl.SSLCertVerificationError) -> str:
    """Причина отказа проверки сертификата и что делать оператору."""
    reason = getattr(exc, "verify_message", None) or str(exc)
    return (
        f"{reason}. Сертификат сервиса не прошёл проверку. "
        "Если сеть перехватывает TLS (корпоративный прокси или антивирус), "
        f"укажите корпоративный корень в файле .pem через переменную "
        f"{CA_BUNDLE_ENV}, либо в стандартной переменной SSL_CERT_FILE. "
        "Проверку сертификатов отключать нельзя: по этому соединению "
        "передаётся ключ авторизации."
    )


def _connect(parts, timeout: int) -> tuple[http.client.HTTPConnection, str]:
    """Соединение с учётом прокси из переменных окружения.

    На служебных сетях доступ к интернету часто идёт через прокси; без его
    учёта запрос не доходит до сервиса.
    """
    secure = parts.scheme == "https"
    default_port = 443 if secure else 80
    target_port = parts.port or default_port
    proxy = urllib.request.getproxies().get(parts.scheme)
    host, port = parts.hostname, target_port
    if proxy:
        proxy_parts = urllib.parse.urlsplit(proxy)
        host = proxy_parts.hostname
        port = proxy_parts.port or default_port
    connection: http.client.HTTPConnection
    if secure:
        connection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl_context()
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
    if proxy and secure:
        connection.set_tunnel(parts.hostname, target_port)
    return connection, host


def _send(url: str, body: bytes, headers: dict, timeout: int) -> tuple[int, str]:
    """POST с телом и точными именами заголовков.

    urllib приводит имена заголовков к виду «Rqid», а протокол требует
    «RqUID», поэтому запрос выполняется напрямую через http.client.
    """
    parts = urllib.parse.urlsplit(url)
    connection, _host = _connect(parts, timeout)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    if urllib.request.getproxies().get(parts.scheme) and parts.scheme == "http":
        # Через прокси путь указывается абсолютным адресом.
        path = urllib.parse.urlunsplit(parts)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8", errors="replace")
    except ssl.SSLCertVerificationError as exc:
        raise GigaChatError(f"Не удалось обратиться к GigaChat: {_ssl_problem(exc)}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise GigaChatError(f"Не удалось обратиться к GigaChat: {exc}") from exc
    finally:
        connection.close()


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    """POST с JSON-телом: сетевая ошибка не превращается в пустой ответ."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, raw = _send(
        url, body,
        {"Content-Type": "application/json", "Accept": "application/json",
         **headers},
        timeout,
    )
    if status >= 400:
        raise GigaChatError(
            f"GigaChat ответил кодом {status}: {raw[:400] or 'без пояснения'}"
        )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise GigaChatError("Ответ GigaChat не является JSON") from exc
    return data if isinstance(data, dict) else {}


def _token_request(authorization_key: str, scope: str, token_url: str,
                   timeout: int) -> dict:
    """Обменять ключ авторизации на access token."""
    credentials = base64.b64encode(
        f"{authorization_key}:".encode("utf-8")
    ).decode("ascii")
    body = urllib.parse.urlencode({"scope": scope}).encode("ascii")
    status, raw = _send(
        token_url, body,
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            # Идентификатор запроса обязателен: без него запрос не
            # обрабатывается, а имя заголовка пишется точно по протоколу.
            "RqUID": str(uuid.uuid4()),
        },
        timeout,
    )
    if status >= 400:
        raise GigaChatError(
            f"Ключ авторизации отклонён (код {status}): "
            f"{raw[:400] or 'без пояснения'}"
        )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise GigaChatError("Ответ со службой токенов не является JSON") from exc
    token = (data.get("access_token") or "") if isinstance(data, dict) else ""
    if not token:
        raise GigaChatError("В ответе со службой токенов нет access_token")
    return data


class GigaChatProvider:
    """Обращение к GigaChat по контексту проекта.

    Провайдер реализует контракт коннектора: на входе контекст, на выходе
    словарь со статусом и предложениями. Текст архивных файлов сюда не
    попадает: коннектор отказывает в таком запросе до вызова провайдера
    (ТЗ п.9, 103).
    """

    def __init__(self, config: GigaChatConfig, key: str | None,
                 clock=time.time):
        self.config = config
        self.key = key
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # -----------------------------------------------------------------
    def configured(self) -> bool:
        """Готов ли провайдер: заданы ключ и модель (ТЗ п.101)."""
        return bool(self.key and self.config.model)

    def access_token(self) -> str:
        """Действующий токен: обмен при первом обращении и по истечении."""
        if self._token and self._clock() < self._token_expires_at:
            return self._token
        if not self.key:
            raise GigaChatError("Не задан ключ авторизации GigaChat")
        answer = _token_request(
            self.key, self.config.scope, self.config.token_url,
            self.config.timeout,
        )
        expires_in = answer.get("expires_in")
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            lifetime = 1800.0
        self._token = answer["access_token"]
        self._token_expires_at = self._clock() + max(
            lifetime - TOKEN_GUARD_SECONDS, 0.0
        )
        return self._token

    def ask(self, prompt: str) -> str:
        """Текстовый запрос к модели."""
        try:
            return self._ask_once(prompt)
        except GigaChatError as exc:
            if "401" not in str(exc):
                raise
            # Токен мог быть отозван или срок вышел раньше расчётного:
            # ключ обменяем заново и повторим запрос один раз.
            log.info("GigaChat отклонил токен, обновляю и повторяю запрос")
            self._token = None
            return self._ask_once(prompt)

    def _ask_once(self, prompt: str) -> str:
        data = _post(
            f"{self.config.base_url}/chat/completions",
            {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                # Ответ нужен краткий: замечания перечисляются списком.
                "temperature": 0.2,
                "max_tokens": 1500,
            },
            {"Authorization": f"Bearer {self.access_token()}"},
            self.config.timeout,
        )
        choices = data.get("choices") or []
        if not choices:
            raise GigaChatError("GigaChat не вернул ни одного ответа")
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()

    def __call__(self, context: dict) -> dict:
        """Контракт коннектора: контекст проекта → предложения."""
        from app.ai.context import describe_context
        from app.ai.provider_result import build_result

        if not self.configured():
            return {
                "status": "not_configured",
                "message": (
                    "Интернет-ИИ не настроен: не задана модель или ключ "
                    "авторизации GigaChat. Проект наружу не передавался "
                    "(ТЗ п.101)."
                ),
                "mode": "INTERNET",
                "proposals": [],
            }
        prompt = (
            f"Запрос оператора: {context.get('request') or 'проверь комплектность'}\n"
            f"Состав сведений: {describe_context(context)}\n\n"
            f"{_context_body(context)}"
        )
        try:
            text = self.ask(prompt)
        except GigaChatError as exc:
            log.warning("GigaChat недоступен: %s", exc)
            return {
                "status": "error",
                "message": f"{exc}. Ответ не подменялся данными проекта.",
                "mode": "INTERNET",
                "proposals": [],
            }
        if not text:
            return {
                "status": "error",
                "message": "GigaChat вернул пустой ответ.",
                "mode": "INTERNET",
                "proposals": [],
            }
        return build_result(text, mode="INTERNET")


def _context_body(context: dict) -> str:
    """Текстовое представление контекста для запроса к модели."""
    lines: list[str] = []
    for row in context.get("documents") or []:
        lines.append(
            f"- документ № {row.get('number') or 'б/н'} «{row.get('type')}»: "
            f"статус {row.get('status')}, выпущен: "
            f"{'да' if row.get('issued') else 'нет'}"
        )
    for row in context.get("archive") or []:
        lines.append(
            f"- архивный документ «{row.get('name') or row.get('title') or 'файл'}»"
            f" ({row.get('category') or 'без категории'})"
        )
    for row in context.get("links") or []:
        lines.append(f"- связь {row.get('role')}: {row.get('from')} → {row.get('to')}")
    for row in context.get("normative") or []:
        lines.append(f"- нормативный документ: {row.get('title') or row.get('id')}")
    for row in context.get("file_texts") or []:
        if row.get("available"):
            lines.append(f"- текст файла «{row.get('name')}»: {row.get('text') or ''}")
    if not lines:
        lines.append("Сведений о проекте нет.")
    return "\n".join(lines)
