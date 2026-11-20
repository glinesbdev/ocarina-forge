"""Errors the server can show to a person who is not looking at a traceback."""


class BancError(Exception):
    """A failure with a stable code and a sentence someone can act on."""

    code = "error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
