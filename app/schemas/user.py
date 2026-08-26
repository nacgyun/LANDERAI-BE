from typing import Any

from pydantic import BaseModel, EmailStr, Field

class UserSignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1)


class UserResponse(BaseModel):
    user_id: str
    email: EmailStr
    name: str
    role: str
    created_at: str
    updated_at: str
    deleted_at: str | None = None
    last_active_project_id: Any | None = None


class CurrentUserResponse(BaseModel):
    user_id: str
    email: EmailStr | None = None
    name: str | None = None
    role: str | None = None
    last_active_project_id: Any | None = None
    created_at: str | None = None
    updated_at: str | None = None
    profile_source: str
