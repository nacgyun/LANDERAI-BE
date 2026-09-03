from typing import Literal

from pydantic import BaseModel, Field, field_validator


RevisionStatus = Literal["QUEUED", "PROCESSING", "COMPLETED", "FAILED"]


class LandingPageRevisionCreateRequest(BaseModel):
    source_revision_id: str = Field(..., min_length=1, max_length=80)
    revision_prompt: str = Field(..., min_length=1, max_length=4000)

    @field_validator("source_revision_id", "revision_prompt")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("빈 문자열은 사용할 수 없습니다.")
        return stripped


class LandingPageRevision(BaseModel):
    revision_id: str
    request_id: str
    source_revision_id: str | None = None
    revision_prompt: str | None = None
    source_type: Literal["VARIANT", "REVISION"]
    source_variant: Literal["A", "B"] | None = None
    html_s3_bucket: str | None = None
    html_s3_key: str | None = None
    created_at: str
    updated_at: str
    processing_started_at: str | None = None
    status: RevisionStatus
    error_type: str | None = None
    error_message: str | None = None


class LandingPageRevisionListResponse(BaseModel):
    items: list[LandingPageRevision]
    total: int
