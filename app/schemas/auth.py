from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["admin", "accountant", "viewer"]


class UserCreate(BaseModel):
    email: EmailStr
    # bcrypt only looks at the first 72 bytes, so longer passwords are rejected rather than truncated.
    password: str = Field(min_length=8, max_length=72)
    full_name: str | None = Field(default=None, max_length=120)
    role: Role = "viewer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str | None
    role: Role
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
