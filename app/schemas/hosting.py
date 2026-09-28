from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


HostingStatus = Literal[
    "UNPUBLISHED", "PUBLISHING", "PUBLISHED", "FAILED", "UNPUBLISHING", "UNPUBLISH_FAILED"
]


class LandingPagePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str | None = Field(None, min_length=1, max_length=80)

    @field_validator("project_name", mode="before")
    @classmethod
    def strip_project_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PydanticCustomError("project_name_type", "프로젝트 이름은 문자열이어야 합니다.")
        stripped = value.strip()
        if not stripped:
            raise PydanticCustomError("project_name_empty", "프로젝트 이름은 빈 문자열일 수 없습니다.")
        if len(stripped) > 80:
            raise PydanticCustomError("project_name_length", "프로젝트 이름은 80자 이내로 입력해 주세요.")
        return stripped


class LandingPageHostingResponse(BaseModel):
    request_id: str
    site_id: str | None = None
    project_name: str | None = None
    site_slug: str | None = None
    hosting_status: HostingStatus
    published_revision_id: str | None = None
    published_url: str | None = None
    published_at: str | None = None
    error_type: str | None = None
    error_message: str | None = None
