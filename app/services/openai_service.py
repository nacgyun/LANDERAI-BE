import json

from openai import OpenAI
from typing import Any

from app.config.settings import settings
from app.config.secrets import get_openai_api_key
from app.prompts.design_plan_prompt import (
    build_design_plan_user_prompt,
    get_design_plan_system_prompt,
)
from app.prompts.landing_page_prompt import (
    build_landing_page_user_prompt,
    get_landing_page_system_prompt,
)
from app.prompts.revision_prompt import (
    build_revision_user_prompt,
    get_revision_system_prompt,
)
from app.schemas.design_plan import DesignPlanCreateRequest, DesignPlanResponse
from app.schemas.landing_page import LandingPageGenerationResponse


LANDING_PAGE_VALIDATION_ATTEMPTS = 2


def _generate_validated_landing_page(
    *,
    client: OpenAI,
    messages: list[dict[str, str]],
    empty_error_message: str,
) -> tuple[LandingPageGenerationResponse, int, int]:
    total_input_tokens = 0
    total_output_tokens = 0
    validation_error: Exception | None = None

    for attempt in range(LANDING_PAGE_VALIDATION_ATTEMPTS):
        attempt_messages = list(messages)
        if validation_error is not None:
            attempt_messages.append(
                {
                    "role": "user",
                    "content": (
                        "The previous output failed server validation. Generate the entire "
                        "JSON response again and fix the following error. Do not explain the fix.\n"
                        f"VALIDATION_ERROR: {validation_error}"
                    ),
                }
            )

        response = client.chat.completions.create(
            model=settings.OPENAI_LANDING_PAGE_MODEL,
            messages=attempt_messages,
            response_format={"type": "json_object"},
        )
        usage = response.usage
        total_input_tokens += usage.prompt_tokens if usage else 0
        total_output_tokens += usage.completion_tokens if usage else 0
        generated_content = response.choices[0].message.content

        try:
            if not generated_content or not generated_content.strip():
                raise ValueError(empty_error_message)
            generated_payload = json.loads(generated_content)
            validated = LandingPageGenerationResponse.model_validate(generated_payload)
            return validated, total_input_tokens, total_output_tokens
        except (json.JSONDecodeError, ValueError) as err:
            validation_error = err
            if attempt == LANDING_PAGE_VALIDATION_ATTEMPTS - 1:
                raise ValueError(
                    "AI 출력이 교정 재시도 후에도 HTML 검증을 통과하지 못했습니다: "
                    f"{err}"
                ) from err

    raise RuntimeError("랜딩페이지 생성 재시도 루프가 예기치 않게 종료되었습니다.")


def get_openai_client() -> OpenAI:
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required for OpenAI requests.")
    return OpenAI(api_key=api_key)


def generate_design_plan_json(
    request: DesignPlanCreateRequest,
    rag_examples: list[dict[str, Any]] | None = None,
) -> tuple[str, int, int]:
    client = get_openai_client()
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
    client = get_openai_client()
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
    client = get_openai_client()
    return _generate_validated_landing_page(
        client=client,
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
        empty_error_message="AI가 생성한 랜딩페이지 JSON이 비어있습니다.",
    )


def revise_landing_page(
    *,
    source_html: str,
    revision_prompt: str,
) -> tuple[LandingPageGenerationResponse, int, int]:
    client = get_openai_client()
    return _generate_validated_landing_page(
        client=client,
        messages=[
            {"role": "system", "content": get_revision_system_prompt()},
            {
                "role": "user",
                "content": build_revision_user_prompt(
                    source_html=source_html,
                    revision_prompt=revision_prompt,
                ),
            },
        ],
        empty_error_message="AI가 생성한 Revision JSON이 비어있습니다.",
    )
