"""AI Connector — граница между приложением и ИИ (ТЗ п.5, 6).

ИИ не имеет прямого доступа к SQLite, файловой системе или исходному коду.
Всё взаимодействие идёт только через разрешённые инструменты прикладного слоя
и требует подтверждения оператора (ТЗ п.11).
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


class AIConnector:
    """Точка входа для ИИ-агента.

    Реализация анализа появится в Этапе 7. Здесь зафиксирован контракт:
    режимы, безопасный статус ответа и отсутствие любых прямых обращений
    к хранилищу.
    """

    def __init__(self, mode: str = MODE_OFF):
        self.mode = mode if mode in MODES else MODE_OFF

    @property
    def enabled(self) -> bool:
        return self.mode != MODE_OFF

    @property
    def mode_label(self) -> str:
        return MODE_LABELS[self.mode]

    def mode_color(self) -> str:
        return MODE_COLORS[self.mode]

    def analyze_package(self, document_ids: list) -> dict:
        """Запрос интеллектуальной проверки комплекта (ТЗ п.102).

        Возвращает только предложения. Применение чего-либо — отдельный шаг,
        требующий подтверждения оператора.
        """
        if not self.enabled:
            return {"status": "disabled", "message": "ИИ выключен", "proposals": []}

        return {
            "status": "success",
            "mode": self.mode,
            "proposals": [
                "Проверьте дату АООК: дата завершения должна быть не раньше АОСР."
            ],
        }
