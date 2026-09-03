from datetime import datetime, timezone
from decimal import Decimal
import json
import uuid

import openai
from botocore.exceptions import BotoCoreError, ClientError

from app.common.workflow_status import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STEP_DESIGN_PLAN,
    STEP_DONE,
    STEP_EMBEDDING,
    STEP_LANDING_PAGE_VARIANT,
    STEP_PERSIST_RESULT,
)
from app.pipelines.landing_page.tasks.design_plan import create_design_plan_with_mutation
from app.pipelines.landing_page.tasks.embedding import create_request_embedding
from app.pipelines.landing_page.tasks.variants import create_landing_page_variants
from app.repositories.request_repository import (
    get_landing_page_request,
    save_landing_page_design_plan,
    save_landing_page_result,
    save_landing_page_result_reference,
    save_landing_page_request_embedding,
    update_landing_page_request_state,
)
from app.repositories.rag_design_plan_repository import (
    get_rag_design_plans_by_request_ids,
)
from app.repositories.s3_repository import upload_landing_page_html
from app.repositories.s3_vector_repository import query_nearest_design_plan_request_ids


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _embedding_to_float_list(embedding: list) -> list[float]:
    return [
        float(value) if isinstance(value, Decimal) else value
        for value in embedding
    ]


def _json_default(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _to_json_safe_value(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {
            key: _to_json_safe_value(child_value)
            for key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_to_json_safe_value(child_value) for child_value in value]
    return value


def _build_rag_examples(rag_design_plans: list[dict]) -> list[dict]:
    examples = []
    for item in rag_design_plans:
        examples.append(
            {
                "source_request": {
                    "request_id": item.get("request_id"),
                    "project_id": item.get("project_id"),
                    "industry": item.get("industry"),
                    "sub_industry": item.get("sub_industry"),
                    "target": item.get("target"),
                    "style": item.get("style"),
                    "goal": item.get("goal"),
                    "additional_context": item.get("additional_context"),
                    "language": item.get("language"),
                    "chosen_variant": item.get("chosen_variant"),
                },
                "selected_design_plan": _to_json_safe_value(item.get("design_plan")),
            }
        )
    return examples


def run_landing_page_pipeline(request_id: str) -> None:
    current_step = STEP_EMBEDDING
    print(f"[Workflow:LOCAL] request_id={request_id} started")
    try:
        request_item = get_landing_page_request(request_id)
        if request_item is None:
            return

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=current_step,
            progress=5,
            error_message=None,
            updated_at=_now(),
        )

        embedding_result = create_request_embedding(request_item)

        rag_examples = []
        try:
            nearest_request_ids = query_nearest_design_plan_request_ids(
                industry=request_item.get("industry"),
                embedding=_embedding_to_float_list(embedding_result["embedding"]),
                top_k=3,
                exclude_request_id=request_id,
            )
            rag_design_plans = get_rag_design_plans_by_request_ids(
                nearest_request_ids,
            )
            print(
                "[RAG] nearest design plans "
                f"for request_id={request_id}: "
                f"{json.dumps(rag_design_plans, ensure_ascii=False, default=_json_default)}"
            )
            rag_examples = _build_rag_examples(rag_design_plans)
        except Exception as rag_err:
            print(
                "[RAG] nearest design plans lookup skipped "
                f"for request_id={request_id}: {rag_err}"
            )

        save_landing_page_request_embedding(
            request_id,
            embedding=embedding_result["embedding"],
            embedding_model=embedding_result["embedding_model"],
            embedding_input=embedding_result["embedding_input"],
            embedding_input_tokens=embedding_result["embedding_input_tokens"],
            updated_at=_now(),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=current_step,
            progress=20,
            updated_at=_now(),
        )

        current_step = STEP_DESIGN_PLAN
        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=current_step,
            progress=30,
            updated_at=_now(),
        )

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
            updated_at=_now(),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=current_step,
            progress=55,
            updated_at=_now(),
        )

        current_step = STEP_LANDING_PAGE_VARIANT
        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=current_step,
            progress=65,
            updated_at=_now(),
        )

        variants_result = create_landing_page_variants(
            design_plan_result["design_plan_json"],
        )
        persisted_variants = {}
        for variant_name, variant_payload in variants_result["variants"].items():
            html_storage = upload_landing_page_html(
                user_id=request_item["user_id"],
                request_id=request_id,
                variant=variant_name,
                html=variant_payload["html"],
            )
            persisted_variants[variant_name] = {
                **variant_payload,
                **html_storage,
            }
            persisted_variants[variant_name].pop("html", None)

        current_step = STEP_PERSIST_RESULT
        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=current_step,
            progress=85,
            updated_at=_now(),
        )

        result_id = f"res_{uuid.uuid4().hex}"
        now = _now()

        save_landing_page_result(
            {
                "result_id": result_id,
                "item_type": "INITIAL_RESULT",
                "request_id": request_id,
                "result_type": "LANDING_PAGE_VARIANTS",
                "variants": persisted_variants,
                "landing_page_input_tokens": variants_result["landing_page_input_tokens"],
                "landing_page_output_tokens": variants_result["landing_page_output_tokens"],
                "landing_page_estimated_cost": variants_result[
                    "landing_page_estimated_cost"
                ],
                "created_at": now,
                "updated_at": now,
            }
        )
        save_landing_page_result_reference(
            request_id,
            landing_result_id=result_id,
            updated_at=_now(),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_COMPLETED,
            current_step=STEP_DONE,
            progress=100,
            updated_at=_now(),
        )
        print(f"[Workflow:LOCAL] request_id={request_id} completed")
    except (BotoCoreError, ClientError, openai.OpenAIError, ValueError) as err:
        error_type = type(err).__name__
        error_message = str(err)
        print(
            "[Workflow:LOCAL] "
            f"request_id={request_id} failed "
            f"current_step={current_step} "
            f"error_type={error_type} error={error_message}"
        )
        try:
            update_landing_page_request_state(
                request_id,
                status=STATUS_FAILED,
                current_step=current_step,
                error_type=error_type,
                error_message=error_message,
                updated_at=_now(),
            )
        except (BotoCoreError, ClientError):
            pass
