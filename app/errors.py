class WumiError(Exception):
    """A refusal the teammate should relay: a stable code plus a plain message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
