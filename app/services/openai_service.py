import json

from openai import OpenAI
from typing import Any

from app.config.settings import settings
from app.prompts.design_plan_prompt import (
    build_design_plan_user_prompt,
    get_design_plan_system_prompt,
)
from app.prompts.landing_page_prompt import (
    build_landing_page_user_prompt,
    get_landing_page_system_prompt,
)
from app.schemas.design_plan import DesignPlanCreateRequest, DesignPlanResponse
from app.schemas.landing_page import LandingPageGenerationResponse


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def generate_design_plan_json(
    request: DesignPlanCreateRequest,
    rag_examples: list[dict[str, Any]] | None = None,
) -> tuple[str, int, int]:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": get_design_plan_system_prompt()},
            {
                "role": "user",
                "content": build_design_plan_user_prompt(request, rag_examples),
            },
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    generated_design_plan = response.choices[0].message.content

    if not generated_design_plan or len(generated_design_plan.strip()) == 0:
        raise ValueError("AI가 생성한 계획 JSON이 비어있습니다.")

    try:
        generated_payload = json.loads(generated_design_plan)
    except json.JSONDecodeError as json_err:
        raise ValueError(f"AI가 유효하지 않은 JSON을 반환했습니다: {json_err}") from json_err

    validated_design_plan = DesignPlanResponse.model_validate(generated_payload)

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    design_plan_json = json.dumps(
        validated_design_plan.model_dump(by_alias=True),
        ensure_ascii=False
    )

    return design_plan_json, input_tokens, output_tokens


def generate_embedding(text: str) -> tuple[list[float], int]:
    response = client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text,
    )

    embedding = response.data[0].embedding
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0

    return embedding, input_tokens


def generate_landing_page_variant(
    *,
    variant: str,
    design_plan: dict[str, Any],
    mutation: dict[str, Any] | None = None,
) -> tuple[LandingPageGenerationResponse, int, int]:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": get_landing_page_system_prompt()},
            {
                "role": "user",
                "content": build_landing_page_user_prompt(
                    variant=variant,
                    design_plan=design_plan,
                    mutation=mutation,
                ),
            },
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    generated_landing_page = response.choices[0].message.content

    if not generated_landing_page or len(generated_landing_page.strip()) == 0:
        raise ValueError("AI가 생성한 랜딩페이지 JSON이 비어있습니다.")

    try:
        generated_payload = json.loads(generated_landing_page)
    except json.JSONDecodeError as json_err:
        raise ValueError(f"AI가 유효하지 않은 JSON을 반환했습니다: {json_err}") from json_err

    validated_landing_page = LandingPageGenerationResponse.model_validate(generated_payload)

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0

    return validated_landing_page, input_tokens, output_tokens
