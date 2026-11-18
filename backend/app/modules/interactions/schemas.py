from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateComment(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    body: str = Field(min_length=1, max_length=2000, strict=True)
    parent_id: UUID | None = None

    @field_validator("body", mode="before")
    @classmethod
    def text(cls, value):
        if not isinstance(value, str) or "\x00" in value or not value.strip():
            raise ValueError("Expected PostgreSQL text")
        return value
