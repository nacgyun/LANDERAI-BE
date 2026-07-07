import json

from app.common.cost import calculate_chat_completion_cost
from app.common.workflow_status import STATUS_PROCESSING, STEP_LANDING_PAGE_VARIANT
from app.lambdas.common import mark_failed, now_iso
from app.pipelines.landing_page.tasks.variants import apply_mutation_to_design_plan
from app.repositories.request_repository import (
    get_landing_page_request,
    update_landing_page_request_state,
)
from app.repositories.s3_repository import upload_landing_page_html
from app.services.openai_service import generate_landing_page_variant


def lambda_handler(event, context):
    request_id = event.get("request_id")
    variant = event.get("variant")
    try:
        if not request_id:
            raise ValueError("request_id is required.")
        if variant not in {"A", "B"}:
            raise ValueError("variant must be A or B.")

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=STEP_LANDING_PAGE_VARIANT,
            progress=65,
            updated_at=now_iso(),
        )

        request_item = get_landing_page_request(request_id)
        if request_item is None:
            raise ValueError(f"LandingPageRequest not found: {request_id}")
        if not request_item.get("design_plan_json"):
            raise ValueError(f"design_plan_json is missing: {request_id}")

        design_plan_payload = json.loads(request_item["design_plan_json"])
        base_plan = design_plan_payload["design_plan"]
        mutation = design_plan_payload["mutation"]

        if variant == "A":
            selected_plan = base_plan
            mutation_for_prompt = None
        else:
            selected_plan = apply_mutation_to_design_plan(base_plan, mutation)
            mutation_for_prompt = mutation

        generated, input_tokens, output_tokens = generate_landing_page_variant(
            variant=variant,
            design_plan=selected_plan,
            mutation=mutation_for_prompt,
        )
        storage = upload_landing_page_html(
            user_id=request_item["user_id"],
            request_id=request_id,
            variant=variant,
            html=generated.html,
        )

        estimated_cost = calculate_chat_completion_cost(
            input_tokens,
            output_tokens,
        )

        return {
            "variant": variant,
            "title": generated.title,
            **storage,
            "landing_page_input_tokens": input_tokens,
            "landing_page_output_tokens": output_tokens,
            "landing_page_estimated_cost": str(estimated_cost),
        }
    except Exception as err:
        mark_failed(request_id, STEP_LANDING_PAGE_VARIANT, err)
        raise

