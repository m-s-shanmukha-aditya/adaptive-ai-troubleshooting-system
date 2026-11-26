
from app.services.troubleshooter import Troubleshooter


def test_windows_rejects_linux_commands():
    response = "Run sudo systemctl start postgresql"

    result = Troubleshooter.validate_response(response, "Windows")

    assert result is False


def test_windows_accepts_windows_instructions():
    response = "Open Services and start PostgreSQL."

    result = Troubleshooter.validate_response(response, "Windows")

    assert result is True


def test_linux_response_is_not_rejected():
    response = "Run sudo systemctl start postgresql"

    result = Troubleshooter.validate_response(response, "Linux")

    assert result is True
