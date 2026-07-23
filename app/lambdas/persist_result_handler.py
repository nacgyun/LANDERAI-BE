import uuid
from decimal import Decimal

from app.common.workflow_status import (
    STATUS_COMPLETED,
    STATUS_PROCESSING,
    STEP_DONE,
    STEP_PERSIST_RESULT,
)
from app.lambdas.common import log_workflow_event, mark_failed, now_iso
from app.repositories.request_repository import (
    save_landing_page_result,
    save_landing_page_result_reference,
    update_landing_page_request_state,
)


def lambda_handler(event, context):
    request_id = event.get("request_id")
    try:
        if not request_id:
            raise ValueError("request_id is required.")

        log_workflow_event(
            step=STEP_PERSIST_RESULT,
            request_id=request_id,
            message="started",
            generated_variant_count=len(event.get("generated_variants", [])),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=STEP_PERSIST_RESULT,
            progress=85,
            updated_at=now_iso(),
        )

        generated_variants = event.get("generated_variants", [])
        if not generated_variants:
            raise ValueError("generated_variants is required.")

        variants = {
            item["variant"]: item
            for item in generated_variants
        }
        total_input_tokens = sum(
            int(item.get("landing_page_input_tokens", 0))
            for item in generated_variants
        )
        total_output_tokens = sum(
            int(item.get("landing_page_output_tokens", 0))
            for item in generated_variants
        )
        total_estimated_cost = sum(
            (
                Decimal(str(item.get("landing_page_estimated_cost", "0")))
                for item in generated_variants
            ),
            Decimal("0"),
        )

        result_id = f"res_{uuid.uuid4().hex}"
        now = now_iso()
        save_landing_page_result(
            {
                "result_id": result_id,
                "request_id": request_id,
                "result_type": "LANDING_PAGE_VARIANTS",
                "variants": variants,
                "landing_page_input_tokens": total_input_tokens,
                "landing_page_output_tokens": total_output_tokens,
                "landing_page_estimated_cost": total_estimated_cost,
                "created_at": now,
                "updated_at": now,
            }
        )
        save_landing_page_result_reference(
            request_id,
            landing_result_id=result_id,
            updated_at=now,
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_COMPLETED,
            current_step=STEP_DONE,
            progress=100,
            updated_at=now,
        )

        log_workflow_event(
            step=STEP_PERSIST_RESULT,
            request_id=request_id,
            message="completed",
            result_id=result_id,
        )

        return {
            "request_id": request_id,
            "result_id": result_id,
            "status": STATUS_COMPLETED,
            "current_step": STEP_DONE,
        }
    except Exception as err:
        mark_failed(request_id, STEP_PERSIST_RESULT, err)
        raise
