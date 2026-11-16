from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    title: str = Field(min_length=1, max_length=200, strict=True)
    description: str = Field(default="", max_length=5000, strict=True)

    @field_validator("title", "description", mode="before")
    @classmethod
    def text(cls, value):
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("Expected text")
        return value.strip()


class UpdateMetadata(Metadata):
    title: str = Field(default="", min_length=1, max_length=200, strict=True)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_ids: list[UUID]

    @field_validator("video_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate video IDs")
        return value
