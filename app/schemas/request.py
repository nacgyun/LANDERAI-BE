from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.design_plan import AllowedIndustry


class LandingPageCreateRequest(BaseModel):
    industry: AllowedIndustry = Field(..., examples=["cafe"])
    sub_industry: str = Field(..., min_length=1, max_length=120, examples=["디저트 카페"])
    target: str = Field(..., min_length=1, max_length=120, examples=["20대 여성"])
    style: str = Field(..., min_length=1, max_length=160, examples=["따뜻하고 감성적인"])
    goal: str = Field(..., min_length=1, max_length=240, examples=["신메뉴 홍보 및 인스타 팔로우 유도"])
    additional_context: str | None = Field(None, max_length=400, examples=["예약 버튼 강조"])
    language: str = Field(...,  min_length=2, max_length=10, examples=["ko"])

    @field_validator(
        "sub_industry",
        "target",
        "style",
        "goal",
        "additional_context",
        "language",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("빈 문자열은 사용할 수 없습니다.")
        return stripped


class LandingPageRequestCreateResponse(BaseModel):
    request_id: str
    status: Literal["QUEUED"]
    current_step: Literal["EMBEDDING"]
    progress: int
    project_id: str | None
    created_at: str


class LandingPageVariantPreview(BaseModel):
    preview_url: str
    html_s3_bucket: str
    html_s3_key: str


class LandingPagePreviewUrlsResponse(BaseModel):
    request_id: str
    status: Literal["COMPLETED"]
    expires_in: int
    variants: dict[str, LandingPageVariantPreview]


class LandingPageVariantSelectionRequest(BaseModel):
    selected_variant: Literal["A", "B"] = Field(..., examples=["A"])


class LandingPageVariantSelectionResponse(BaseModel):
    request_id: str
    status: Literal["COMPLETED"]
    selection_status: Literal["SELECTED", "NOT_SELECTED"]
    chosen_variant: Literal["A", "B"] | None
    selected_design_plan_id: str | None = None
    design_plan_vector_key: str | None = None
