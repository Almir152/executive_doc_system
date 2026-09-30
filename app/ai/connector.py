"""AI Connector — граница между приложением и ИИ (ТЗ п.5, 6, 9, 101).

ИИ не имеет прямого доступа к SQLite, файловой системе или исходному коду.
Всё взаимодействие идёт только через разрешённые инструменты прикладного слоя
и требует подтверждения оператора (ТЗ п.11).

ИИ — заменяемый компонент (ТЗ п.101): приложение знает только этот контракт.
Коннектор получает готовый контекст (ТЗ п.103) и возвращает предложения;
ни один из режимов не меняет данные сам — это делает прикладной API после
подтверждения оператора (ТЗ п.104).
"""

# ТЗ п.9: три режима работы.
MODE_OFF = "OFF"
MODE_LOCAL = "LOCAL"
MODE_INTERNET = "INTERNET"

MODES = (MODE_OFF, MODE_LOCAL, MODE_INTERNET)

MODE_LABELS = {
    MODE_OFF: "Нет ИИ",
    MODE_LOCAL: "Локальный ИИ",
    MODE_INTERNET: "Интернет-ИИ",
}

# ТЗ п.9: индикация — зелёный локальный, красный интернет, выключено — нет.
MODE_COLORS = {
    MODE_OFF: "#666666",
    MODE_LOCAL: "#1a7f37",
    MODE_INTERNET: "#c1121f",
}

MODE_ORDER = (MODE_LOCAL, MODE_INTERNET, MODE_OFF)

STATUS_DISABLED = "disabled"
STATUS_SUCCESS = "success"
STATUS_NOT_CONFIGURED = "not_configured"
#: Отказ по границе доступа: данные нельзя передавать в этом режиме.
STATUS_REFUSED = "refused"


def _has_file_text(context: dict) -> bool:
    """Есть ли в контексте текст файлов, отмеченный оператором (ТЗ п.103)."""
    return any(
        row.get("available") for row in (context.get("file_texts") or [])
    )


class AIConnector:
    """Точка входа для ИИ-агента.

    Локальный режим работает как набор проверок над контекстом: он
    предсказуем, ничего не выдумывает и не требует внешних сервисов.
    Интернет-режим без настроенного провайдера честно сообщает об этом,
    а не изображает анализ (ТЗ п.101, 106).
    """

    def __init__(self, mode: str = MODE_OFF, provider=None):
        self._mode = mode if mode in MODES else MODE_OFF
        self.provider = provider

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        """Неизвестный режим выключает ИИ, а не ломает интерфейс (ТЗ п.9)."""
        self._mode = value if value in MODES else MODE_OFF

    @property
    def enabled(self) -> bool:
        return self.mode != MODE_OFF

    @property
    def mode_label(self) -> str:
        return MODE_LABELS[self.mode]

    def mode_color(self) -> str:
        return MODE_COLORS[self.mode]

    def analyze(self, context: dict) -> dict:
        """Проверить контекст проекта и вернуть предложения (ТЗ п.102).

        Возвращает только предложения: ни одно изменение данных здесь не
        происходит (ТЗ п.104).
        """
        if not self.enabled:
            return {
                "status": STATUS_DISABLED,
                "message": "ИИ выключен: включите его в настройках (ТЗ п.9).",
                "mode": self.mode,
                "proposals": [],
            }
        if self.mode == MODE_INTERNET and _has_file_text(context):
            # Текст документов наружу не уходит: для этого нужно отдельное
            # решение оператора, а не молчаливая отправка вместе с запросом.
            return {
                "status": STATUS_REFUSED,
                "message": (
                    "Отмечены файлы, текст которых нельзя передавать в "
                    "интернет-режиме. Снимите отметки или переключитесь на "
                    "локальный ИИ: текст файла остаётся на компьютере "
                    "(ТЗ п.9, 103)."
                ),
                "mode": self.mode,
                "proposals": [],
            }
        if self.provider is not None:
            return self.provider(context)
        if self.mode == MODE_INTERNET:
            return {
                "status": STATUS_NOT_CONFIGURED,
                "message": (
                    "Интернет-ИИ не настроен: не задана модель и не подключён "
                    "провайдер. Документы и проект не передавались наружу "
                    "(ТЗ п.101)."
                ),
                "mode": self.mode,
                "proposals": [],
            }
        from app.ai.rules import analyze_context

        return {
            "status": STATUS_SUCCESS,
            "mode": self.mode,
            "message": "",
            "proposals": analyze_context(context),
        }

    def analyze_package(self, document_ids: list) -> dict:
        """Совместимость с прежним вызовом интерфейса (ТЗ п.102).

        Идентификаторы документов без контекста проверить нечем, поэтому
        возвращается честный отказ с пояснением вместо правдоподобного
        текста (ТЗ п.106).
        """
        return {
            "status": STATUS_NOT_CONFIGURED,
            "message": (
                "Для проверки нужен контекст проекта: выберите проект и "
                "сформулируйте запрос (ТЗ п.102, 103)."
            ),
            "document_ids": list(document_ids or []),
            "proposals": [],
        }


def build_internet_provider():
    """Провайдер для интернет-режима по сохранённым настройкам.

    Возвращает ``None``, если ключ или модель не заданы: тогда коннектор
    честно сообщает о ненастроенном ИИ, а не обращается наружу (ТЗ п.101).
    """
    from app.ai.gigachat import GigaChatConfig, GigaChatProvider
    from app.ai.secrets import GIGACHAT_KEY, STORE

    key = STORE.get(GIGACHAT_KEY)
    config = GigaChatConfig.from_settings()
    if not key and not config.model:
        return None
    provider = GigaChatProvider(config, key)
    return provider if provider.configured() else None
