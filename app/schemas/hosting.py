from typing import Literal

from pydantic import BaseModel, Field, field_validator


HostingStatus = Literal[
    "UNPUBLISHED", "PUBLISHING", "PUBLISHED", "FAILED", "UNPUBLISHING", "UNPUBLISH_FAILED"
]


class LandingPagePublishRequest(BaseModel):
    project_name: str | None = Field(None, min_length=1, max_length=80)

    @field_validator("project_name")
    @classmethod
    def strip_project_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("프로젝트 이름은 빈 문자열일 수 없습니다.")
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
