"""Хранение ключей доступа к внешним сервисам ИИ (ТЗ п.101).

Ключ GigaChat — это пароль, поэтому в открытом виде он не хранится там,
где его может прочитать посторонний. Порядок хранения:

1. Windows DPAPI — ключ шифруется средствами системы и расшифровывается
   только под учётной записью текущего пользователя;
2. переменная окружения — режим разработки и проверки на другой машине;
3. файл настроек — последний вариант: значение сохраняется открытым,
   приложение предупреждает об этом и переносит его в DPAPI при первой
   возможности.

Открытый вариант в файле настроек допускается только потому, что иначе
пользователь не смог бы начать работу: молча не сохранять ключ значит
каждый раз вводить его заново. Предупреждение при этом обязательно.
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import sys
from ctypes import wintypes

from app.config import SETTINGS_PATH

log = logging.getLogger(__name__)

#: Ключ авторизации GigaChat.
GIGACHAT_KEY = "gigachat_authorization_key"

#: Переменная окружения с ключом: используется в разработке и проверках.
ENV_KEY = "EXECUTIVE_DOC_GIGACHAT_KEY"

BACKEND_DPAPI = "dpapi"
BACKEND_ENV = "env"
BACKEND_PLAIN = "plain"

SECRETS_FILE_KEY = "secrets"


def dpapi_available() -> bool:
    """Доступно ли шифрование средствами Windows (DPAPI)."""
    return sys.platform == "win32"


class _DataBlob(ctypes.Structure):
    """Структура DATA_BLOB из crypt32.dll."""

    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


#: Ключ не читается другим пользователем и другим компьютером.
_ENTROPY = b"executive_doc_system.gigachat"
#: CRYPTPROTECT_UI_FORBIDDEN: без интерактивных окон и запросов.
_UI_FORBIDDEN = 0x01


def _blob(payload: bytes) -> tuple:
    """DATA_BLOB и буфер, который обязан остаться живым до вызова API."""
    buffer = ctypes.create_string_buffer(payload, len(payload))
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    return _DataBlob(len(payload), pointer), buffer


def _prepare_api():
    """Прототипы функций: без них вызов на 64-разрядной системе неверен."""
    from ctypes import wintypes as w

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    for name in ("CryptProtectData", "CryptUnprotectData"):
        function = getattr(crypt32, name)
        function.restype = w.BOOL
        function.argtypes = [
            ctypes.POINTER(_DataBlob),      # pDataIn
            w.LPWSTR,                       # описание данных / *ppszDataDescr
            ctypes.POINTER(_DataBlob),      # pOptionalEntropy
            ctypes.c_void_p,                # pvReserved
            ctypes.c_void_p,                # pPromptStruct
            w.DWORD,                        # dwFlags
            ctypes.POINTER(_DataBlob),      # pDataOut
        ]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return crypt32, kernel32


def _dpapi_protect(data: bytes) -> bytes:
    """Зашифровать данные ключом текущего пользователя Windows."""
    crypt32, kernel32 = _prepare_api()
    entropy, entropy_buffer = _blob(_ENTROPY)
    data_blob, data_buffer = _blob(data)
    out_blob = _DataBlob()
    try:
        ok = crypt32.CryptProtectData(
            ctypes.byref(data_blob), ctypes.c_wchar_p("GigaChat"),
            ctypes.byref(entropy), None, None, _UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise ctypes.WinError()
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))
        del data_buffer, entropy_buffer


def _dpapi_unprotect(payload: bytes) -> bytes:
    """Расшифровать данные, защищённые CryptProtectData."""
    crypt32, kernel32 = _prepare_api()
    entropy, entropy_buffer = _blob(_ENTROPY)
    data_blob, data_buffer = _blob(payload)
    out_blob = _DataBlob()
    try:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(data_blob), None, ctypes.byref(entropy), None, None,
            _UI_FORBIDDEN, ctypes.byref(out_blob),
        )
        if not ok:
            raise ctypes.WinError()
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))
        del data_buffer, entropy_buffer


class SecretStore:
    """Ключи приложения с выбором способа хранения.

    Пустое значение читается как «ключа нет»: приложение не падает и не
    изображает настроенный ИИ (ТЗ п.101, 106).
    """

    def __init__(self, path=None):
        self.path = path or SETTINGS_PATH
        self._cache: dict[str, str] = {}

    # -----------------------------------------------------------------
    # Чтение и запись файла настроек
    # -----------------------------------------------------------------
    def _read_settings(self) -> dict:
        """Всё содержимое файла настроек: ключи лежат рядом с прочими."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            log.warning("Файл настроек повреждён, ключи ИИ не прочитаны: %s",
                        self.path)
            return {}
        return data if isinstance(data, dict) else {}

    def _read_secrets(self) -> dict:
        entries = self._read_settings().get(SECRETS_FILE_KEY)
        return entries if isinstance(entries, dict) else {}

    def _write_entry(self, name: str, entry: dict | None) -> None:
        data = self._read_settings()
        entries = data.get(SECRETS_FILE_KEY)
        entries = dict(entries) if isinstance(entries, dict) else {}
        if entry is None:
            entries.pop(name, None)
        else:
            entries[name] = entry
        if entries:
            data[SECRETS_FILE_KEY] = entries
        else:
            data.pop(SECRETS_FILE_KEY, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _decode(self, entry: dict) -> str | None:
        """Значение из записи файла настроек."""
        backend = entry.get("backend")
        raw = entry.get("value")
        if not isinstance(raw, str) or not raw:
            return None
        if backend == BACKEND_DPAPI:
            try:
                return _dpapi_unprotect(base64.b64decode(raw)).decode("utf-8")
            except (OSError, ValueError) as exc:
                log.warning("Ключ, сохранённый средствами Windows, не прочитан: %s", exc)
                return None
        if backend == BACKEND_PLAIN:
            return raw
        return None

    # -----------------------------------------------------------------
    # Публичный интерфейс
    # -----------------------------------------------------------------
    def get(self, name: str) -> str | None:
        """Ключ из защищённого хранилища, переменной окружения или файла."""
        if name in self._cache:
            return self._cache[name]
        env_value = os.environ.get(ENV_KEY) if name == GIGACHAT_KEY else None
        if env_value:
            log.debug("ключ %s взят из переменной окружения", name)
            self._cache[name] = env_value
            return env_value
        entry = self._read_secrets().get(name)
        if not isinstance(entry, dict):
            return None
        value = self._decode(entry)
        if value:
            self._cache[name] = value
        return value

    def set(self, name: str, value: str) -> str:
        """Сохранить ключ и вернуть фактически использованный способ хранения."""
        value = (value or "").strip()
        self._cache.pop(name, None)
        if not value:
            self._write_entry(name, None)
            return BACKEND_PLAIN
        if dpapi_available():
            try:
                encrypted = _dpapi_protect(value.encode("utf-8"))
            except OSError as exc:
                log.warning("DPAPI недоступен (%s), ключ сохранён открытым", exc)
            else:
                self._write_entry(name, {
                    "backend": BACKEND_DPAPI,
                    "value": base64.b64encode(encrypted).decode("ascii"),
                })
                return BACKEND_DPAPI
        self._write_entry(name, {"backend": BACKEND_PLAIN, "value": value})
        return BACKEND_PLAIN

    def forget(self, name: str) -> None:
        """Забыть ключ: значение удаляется из настроек и памяти."""
        self._cache.pop(name, None)
        self._write_entry(name, None)

    def backend_of(self, name: str) -> str | None:
        """Где физически лежит ключ: DPAPI, окружение или открытый файл."""
        if name == GIGACHAT_KEY and os.environ.get(ENV_KEY):
            return BACKEND_ENV
        entry = self._read_secrets().get(name)
        if isinstance(entry, dict):
            backend = entry.get("backend")
            return backend if backend in (BACKEND_DPAPI, BACKEND_PLAIN) else None
        return None

    def migrate_plain_to_dpapi(self, name: str) -> str:
        """Перенести открыто сохранённый ключ в DPAPI.

        Вызывается при запуске: если система умеет шифровать, открытое
        значение перестаёт храниться (ТЗ п.101).
        """
        if not dpapi_available() or self.backend_of(name) != BACKEND_PLAIN:
            return ""
        value = self.get(name)
        if not value:
            return ""
        backend = self.set(name, value)
        return "" if backend == BACKEND_DPAPI else backend

    def status(self, name: str) -> dict:
        """Состояние хранения ключа для интерфейса настроек."""
        backend = self.backend_of(name)
        present = bool(self.get(name))
        warnings = []
        if present and backend == BACKEND_PLAIN:
            warnings.append(
                "Ключ сохранён открытым в файле настроек: он доступен любому, "
                "кто прочитает файл. На Windows он будет перенесён в "
                "защищённое хранилище при следующем запуске."
            )
        if present and not backend:
            warnings.append("Не удалось определить, где сохранён ключ.")
        return {
            "present": present,
            "backend": backend,
            "protected": backend == BACKEND_DPAPI or backend == BACKEND_ENV,
            "warnings": warnings,
        }


#: Хранилище приложения по умолчанию.
STORE = SecretStore()
