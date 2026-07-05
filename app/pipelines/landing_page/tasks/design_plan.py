from app.common.cost import calculate_chat_completion_cost
from app.schemas.design_plan import DesignPlanCreateRequest
from app.services.openai_service import generate_design_plan_json


def build_design_plan_request(request_item: dict) -> DesignPlanCreateRequest:
    return DesignPlanCreateRequest(
        industry=request_item["industry"],
        sub_industry=request_item["sub_industry"],
        target=request_item["target"],
        style=request_item["style"],
        purpose=request_item["goal"],
        extra=request_item.get("additional_context"),
        language=request_item.get("language", "ko"),
    )


def create_design_plan_with_mutation(
    request_item: dict,
    rag_examples: list[dict] | None = None,
) -> dict:
    design_plan_request = build_design_plan_request(request_item)
    design_plan_json, input_tokens, output_tokens = generate_design_plan_json(
        design_plan_request,
        rag_examples=rag_examples,
    )
    estimated_cost = calculate_chat_completion_cost(input_tokens, output_tokens)

    return {
        "design_plan_json": design_plan_json,
        "design_plan_input_tokens": input_tokens,
        "design_plan_output_tokens": output_tokens,
        "design_plan_estimated_cost": estimated_cost,
    }
