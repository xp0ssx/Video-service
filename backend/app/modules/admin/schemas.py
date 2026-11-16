from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    role: Literal["user", "moderator"]
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Reason is required")
        return value


class AdminUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    role: Literal["user", "moderator", "admin"]
