from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.mutation_paths import normalize_mutation_path


AllowedIndustry = Literal[
    "cafe",
    "restaurant",
    "medical",
    "education",
    "shopping",
    "fitness_beauty",
    "it",
    "professional_service",
    "other",
]


class DesignPlanCreateRequest(BaseModel):
    industry: AllowedIndustry = Field(..., examples=["cafe"], description="업종 정보")
    sub_industry: str = Field(..., min_length=1, max_length=120, examples=["브런치 카페"], description="세부 업종 정보")
    target: str = Field(..., min_length=1, max_length=120, examples=["20대 여성"], description="주요 타겟층")
    style: str = Field(..., min_length=1, max_length=160, examples=["따뜻하고 감성적인"], description="디자인/콘텐츠 스타일")
    purpose: str = Field(..., min_length=1, max_length=240, examples=["신메뉴 홍보 및 인스타 팔로우 유도"], description="랜딩페이지 목적")
    extra: Optional[str] = Field(None, max_length=400, examples=["검은색 위주, 예약 버튼 강조"], description="선택적 추가 요청사항")
    language: str = Field("ko", min_length=2, max_length=10, examples=["ko"], description="생성 언어")

    @field_validator("industry", "sub_industry", "target", "style", "purpose", "extra", "language")
    @classmethod
    def strip_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("빈 문자열은 사용할 수 없습니다.")
        return stripped


AllowedSectionType = Literal[
    "hero",
    "problem",
    "solution",
    "features",
    "benefits",
    "trust",
    "social_proof",
    "pricing",
    "faq",
    "final_cta",
]
AllowedMutationAxis = Literal[
    "message_framing",
    "conversion_strategy",
    "trust_emphasis",
    "benefit_emphasis",
    "urgency_level",
    "information_density",
    "visual_direction",
    "section_priority",
]


class DesignPlanMeta(BaseModel):
    industry: AllowedIndustry
    target: str
    goal: str
    input_summary: str
    language: str


class DesignPlanStrategy(BaseModel):
    core_message: str
    user_pain: str
    user_motivation: str
    positioning: str
    objection: str


class StyleVector(BaseModel):
    tone: float = Field(..., ge=0.0, le=1.0)
    intensity: float = Field(..., ge=0.0, le=1.0)
    formality: float = Field(..., ge=0.0, le=1.0)
    density: float = Field(..., ge=0.0, le=1.0)
    urgency: float = Field(..., ge=0.0, le=1.0)


class SectionPlan(BaseModel):
    id: str
    type: AllowedSectionType
    intent: str
    message: str
    required_elements: list[str] = Field(..., min_length=1)


class VisualRules(BaseModel):
    color_theme: str
    typography: str
    spacing: str
    button_style: str


class ConversionStrategy(BaseModel):
    hook: str
    primary_cta: str
    secondary_cta: str
    trust_elements: list[str]
    risk_reducers: list[str]


class DesignPlan(BaseModel):
    meta: DesignPlanMeta
    strategy: DesignPlanStrategy
    style_vector: StyleVector
    structure: list[SectionPlan] = Field(..., min_length=4, max_length=7)
    visual_rules: VisualRules
    conversion_strategy: ConversionStrategy

    @field_validator("structure")
    @classmethod
    def validate_structure_edges(cls, value: list[SectionPlan]) -> list[SectionPlan]:
        if value[0].type != "hero":
            raise ValueError("첫 번째 section type은 hero여야 합니다.")
        if value[-1].type != "final_cta":
            raise ValueError("마지막 section type은 final_cta여야 합니다.")
        return value


class MutationPatch(BaseModel):
    path: str
    value: Any

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_mutation_path(value)


class StrategyShift(BaseModel):
    from_: str = Field(..., alias="from")
    to: str

    model_config = {"populate_by_name": True}


class MutationPlan(BaseModel):
    name: str
    level: Literal["plan_strategy"]
    axis: list[AllowedMutationAxis] = Field(..., min_length=1, max_length=2)
    hypothesis: str
    strategy_shift: StrategyShift
    changed_fields: list[str] = Field(..., min_length=1)
    control_rules: list[str] = Field(..., min_length=3)
    patch: list[MutationPatch] = Field(..., min_length=1)

    @field_validator("changed_fields")
    @classmethod
    def validate_changed_fields(cls, value: list[str]) -> list[str]:
        return [normalize_mutation_path(path) for path in value]

    @model_validator(mode="after")
    def validate_patch_matches_changed_fields(self) -> "MutationPlan":
        changed_field_set = set(self.changed_fields)
        patch_path_set = {patch.path for patch in self.patch}
        if patch_path_set != changed_field_set:
            raise ValueError("mutation.patch.path 값은 changed_fields와 정확히 일치해야 합니다.")
        return self


class DesignPlanResponse(BaseModel):
    design_plan: DesignPlan
    mutation: MutationPlan
