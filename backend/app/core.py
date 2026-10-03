from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, code: str = "app_error", step: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.step = step
        self.details = details or {}


class MissingConfigurationError(AppError):
    def __init__(self, name: str, step: str | None = None):
        super().__init__(f"缺少必要环境变量：{name}", code="missing_configuration", step=step)
