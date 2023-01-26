class gracyException(Exception):
    pass


class NonOkResponse(gracyException):
    def __init__(self, url: str, response) -> None:
        super().__init__(*args)

    pass
