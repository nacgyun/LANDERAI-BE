from typing import Literal

from pydantic import BaseModel, Field, field_validator


FORBIDDEN_HTML_PATTERNS = [
    "<script src=",
    "<link",
    "@import",
    "cdn.tailwindcss.com",
    "bootstrap",
    "bulma",
]


class LandingPageGenerationResponse(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    html: str = Field(..., min_length=1)

    @field_validator("html")
    @classmethod
    def validate_html(cls, value: str) -> str:
        stripped = value.strip()
        html_lower = stripped.lower()

        if "<html" not in html_lower:
            raise ValueError("html 필드는 완전한 HTML 문서를 포함해야 합니다.")
        if "<style" not in html_lower or "</style>" not in html_lower:
            raise ValueError("html 필드는 inline style 태그를 포함해야 합니다.")
        if "<script" not in html_lower or "</script>" not in html_lower:
            raise ValueError("html 필드는 inline script 태그를 포함해야 합니다.")
        for pattern in FORBIDDEN_HTML_PATTERNS:
            if pattern in html_lower:
                raise ValueError(f"외부 CSS/JS 의존성은 사용할 수 없습니다: {pattern}")

        return stripped


class LandingPageVariantResult(BaseModel):
    variant: Literal["A", "B"]
    title: str
    html: str
    plan: dict
