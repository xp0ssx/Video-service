from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    visibility: Literal["public", "private"] = "private"

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Title required")
        return value


class UpdateVideo(CreateVideo):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    visibility: Literal["public", "private"] | None = None

    @field_validator("title", "description", "visibility", mode="before")
    @classmethod
    def no_null(cls, value):
        if value is None:
            raise ValueError("Explicit null is not allowed")
        return value
