"""Stable public errors. Never include rejected input or exception text."""


class ScsError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details

    def result(self) -> dict:
        result = {"status": "ERROR", "error_code": self.code, "message": str(self)}
        if self.details is not None:
            result["details"] = self.details
        return result
