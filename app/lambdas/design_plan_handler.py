from app.common.workflow_status import STATUS_PROCESSING, STEP_DESIGN_PLAN
from app.lambdas.common import (
    build_rag_examples,
    log_workflow_event,
    mark_failed,
    now_iso,
)
from app.pipelines.landing_page.tasks.design_plan import create_design_plan_with_mutation
from app.repositories.rag_design_plan_repository import get_rag_design_plans_by_request_ids
from app.repositories.request_repository import (
    get_landing_page_request,
    save_landing_page_design_plan,
    update_landing_page_request_state,
)


def lambda_handler(event, context):
    request_id = event.get("request_id")
    rag_request_ids = event.get("rag_request_ids", [])
    try:
        if not request_id:
            raise ValueError("request_id is required.")

        log_workflow_event(
            step=STEP_DESIGN_PLAN,
            request_id=request_id,
            message="started",
            rag_request_count=len(rag_request_ids),
            rag_request_ids=",".join(rag_request_ids),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=STEP_DESIGN_PLAN,
            progress=35,
            updated_at=now_iso(),
        )

        request_item = get_landing_page_request(request_id)
        if request_item is None:
            raise ValueError(f"LandingPageRequest not found: {request_id}")

        rag_design_plans = get_rag_design_plans_by_request_ids(rag_request_ids)
        rag_examples = build_rag_examples(rag_design_plans)

        design_plan_result = create_design_plan_with_mutation(
            request_item,
            rag_examples=rag_examples,
        )

        save_landing_page_design_plan(
            request_id,
            design_plan_json=design_plan_result["design_plan_json"],
            design_plan_input_tokens=design_plan_result["design_plan_input_tokens"],
            design_plan_output_tokens=design_plan_result["design_plan_output_tokens"],
            design_plan_estimated_cost=design_plan_result["design_plan_estimated_cost"],
            updated_at=now_iso(),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=STEP_DESIGN_PLAN,
            progress=55,
            updated_at=now_iso(),
        )

        log_workflow_event(
            step=STEP_DESIGN_PLAN,
            request_id=request_id,
            message="completed",
        )

        return {
            "request_id": request_id,
            "variants": ["A", "B"],
        }
    except Exception as err:
        mark_failed(request_id, STEP_DESIGN_PLAN, err)
        raise
