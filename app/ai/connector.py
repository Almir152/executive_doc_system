class AIConnector:
    """
    Разделы 5-11 ТЗ: ИИ не имеет прямого доступа к БД или ФС.
    Все действия только через согласование с пользователем.
    """
    def __init__(self, mode="OFF"):  # OFF, LOCAL, INTERNET
        self.mode = mode

    def analyze_package(self, document_ids: list) -> dict:
        if self.mode == "OFF":
            return {"status": "disabled", "message": "ИИ выключен"}
        
        return {
            "status": "success",
            "proposals": [
                "Проверьте дату АООК: дата завершения должна быть не раньше АОСР."
            ]
        }
