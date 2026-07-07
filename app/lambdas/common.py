from datetime import datetime, timezone
from decimal import Decimal

from app.common.workflow_status import STATUS_FAILED
from app.repositories.request_repository import update_landing_page_request_state


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decimal_to_float_list(embedding: list) -> list[float]:
    return [
        float(value) if isinstance(value, Decimal) else value
        for value in embedding
    ]


def to_json_safe_value(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {
            key: to_json_safe_value(child_value)
            for key, child_value in value.items()
        }
    if isinstance(value, list):
        return [to_json_safe_value(child_value) for child_value in value]
    return value


def build_rag_examples(rag_design_plans: list[dict]) -> list[dict]:
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
                "selected_design_plan": to_json_safe_value(item.get("design_plan")),
            }
        )
    return examples


def mark_failed(request_id: str | None, current_step: str, err: Exception) -> None:
    if not request_id:
        return

    try:
        update_landing_page_request_state(
            request_id,
            status=STATUS_FAILED,
            current_step=current_step,
            error_message=str(err),
            updated_at=now_iso(),
        )
    except Exception:
        pass

