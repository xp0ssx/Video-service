import pytest
from pydantic import ValidationError

from app.modules.moderation.schemas import CreateReport, Decision, StateChange


@pytest.mark.parametrize("body", ["", "   ", "x" * 2001, "\x00"])
def test_invalid_report_text(body):
    with pytest.raises(ValidationError):
        CreateReport(reason="spam", body=body)


def test_report_and_decision_validation():
    assert CreateReport(reason="other", body="  Text  ").body == "Text"
    for data in (
        {"reason": "invalid", "body": "x"},
        {"reason": "spam", "body": "x", "reporter_id": "fake"},
    ):
        with pytest.raises(ValidationError):
            CreateReport(**data)
    with pytest.raises(ValidationError):
        Decision(status="open", reason="x")
    with pytest.raises(ValidationError):
        StateChange(state="deleted", reason="x")
