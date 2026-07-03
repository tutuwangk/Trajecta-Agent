import logging

from app.core import api_error


def test_api_error_logs_unexpected_exception(caplog):
    caplog.set_level(logging.ERROR)

    response = api_error(TypeError("unhashable type: 'dict'"), {"plan": "failed"})

    assert response["error"]["code"] == "internal_error"
    assert "Unexpected error while handling request" in caplog.text
    assert "unhashable type" in caplog.text
