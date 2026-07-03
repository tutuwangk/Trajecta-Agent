from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, message: str, code: str = "app_error", step: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.step = step


class MissingConfigurationError(AppError):
    def __init__(self, name: str, step: str | None = None):
        super().__init__(f"缺少必要环境变量：{name}", code="missing_configuration", step=step)


def api_success(data: dict | list | None = None, step_status: dict | None = None) -> dict:
    return {"ok": True, "data": data, "error": None, "step_status": step_status or {}}


def api_error(error: Exception, step_status: dict | None = None) -> dict:
    if isinstance(error, AppError):
        payload = {"code": error.code, "message": error.message, "step": error.step}
    else:
        logger.exception("Unexpected error while handling request", exc_info=error)
        payload = {"code": "internal_error", "message": "服务处理失败，请稍后重试。", "step": None}
    return {"ok": False, "data": None, "error": payload, "step_status": step_status or {}}
