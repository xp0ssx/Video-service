import unicodedata

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


def normalize(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text")
    return unicodedata.normalize("NFKC", value).strip().casefold()


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def email_normalized(cls, value: str) -> str:
        return normalize(value)


class Register(Login):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_]+$")

    @field_validator("username", mode="before")
    @classmethod
    def username_normalized(cls, value: str) -> str:
        return normalize(value)
