"""Ошибки интеграции Авито с текстом для пользователя и API."""


class AvitoAutoCreateUnavailableError(Exception):
    """
    Публичный каталог API Авито не принимает создание черновика через
    POST /core/v1/accounts/{user_id}/items/0/ (типично HTTP 405).
    """

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message
