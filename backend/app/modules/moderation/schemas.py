from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    reason: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("reason")
    @classmethod
    def text(cls, value):
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Nonempty text required")
        return value


class CreateReport(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    reason: Literal["spam", "copyright", "abuse", "other"]
    body: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("body")
    @classmethod
    def body_text(cls, value):
        return Reason(reason=value).reason


class Decision(Reason):
    status: Literal["dismissed", "actioned"]


class StateChange(Reason):
    state: Literal["hidden", "visible"]
