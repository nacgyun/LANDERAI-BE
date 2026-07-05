from decimal import Decimal
from typing import Any

from app.repositories.dynamodb_setup import get_request_table, get_result_table


def _to_dynamodb_safe_item(item: dict[str, Any]) -> dict[str, Any]:
    return _to_dynamodb_safe_value(item)


def _to_dynamodb_safe_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {
            key: _to_dynamodb_safe_value(child_value)
            for key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_to_dynamodb_safe_value(child_value) for child_value in value]
    return value


def save_landing_page_request(item: dict[str, Any]) -> None:
    get_request_table().put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(request_id)",
    )


def get_landing_page_request(request_id: str) -> dict[str, Any] | None:
    response = get_request_table().get_item(
        Key={
            "request_id": request_id,
        }
    )
    return response.get("Item")


def update_landing_page_request_state(
    request_id: str,
    *,
    status: str | None = None,
    current_step: str | None = None,
    progress: int | None = None,
    error_message: str | None = None,
    updated_at: str,
) -> None:
    update_parts = ["updated_at = :updated_at"]
    expression_values: dict[str, Any] = {
        ":updated_at": updated_at,
    }
    expression_names: dict[str, str] = {}

    if status is not None:
        update_parts.append("#status = :status")
        expression_names["#status"] = "status"
        expression_values[":status"] = status
    if current_step is not None:
        update_parts.append("current_step = :current_step")
        expression_values[":current_step"] = current_step
    if progress is not None:
        update_parts.append("progress = :progress")
        expression_values[":progress"] = progress
    if error_message is not None:
        update_parts.append("error_message = :error_message")
        expression_values[":error_message"] = error_message

    update_kwargs: dict[str, Any] = {
        "Key": {
            "request_id": request_id,
        },
        "UpdateExpression": f"SET {', '.join(update_parts)}",
        "ConditionExpression": "attribute_exists(request_id)",
        "ExpressionAttributeValues": expression_values,
    }
    if expression_names:
        update_kwargs["ExpressionAttributeNames"] = expression_names

    get_request_table().update_item(**update_kwargs)


def save_landing_page_request_embedding(
    request_id: str,
    *,
    embedding: list[Any],
    embedding_model: str,
    embedding_input: str,
    embedding_input_tokens: int,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={
            "request_id": request_id,
        },
        UpdateExpression=(
            "SET request_embedding = :request_embedding, "
            "embedding_model = :embedding_model, "
            "embedding_input = :embedding_input, "
            "embedding_input_tokens = :embedding_input_tokens, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeValues={
            ":request_embedding": embedding,
            ":embedding_model": embedding_model,
            ":embedding_input": embedding_input,
            ":embedding_input_tokens": embedding_input_tokens,
            ":updated_at": updated_at,
        },
    )


def save_landing_page_design_plan(
    request_id: str,
    *,
    design_plan_json: str,
    design_plan_input_tokens: int,
    design_plan_output_tokens: int,
    design_plan_estimated_cost: Any,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={
            "request_id": request_id,
        },
        UpdateExpression=(
            "SET design_plan_json = :design_plan_json, "
            "design_plan_input_tokens = :design_plan_input_tokens, "
            "design_plan_output_tokens = :design_plan_output_tokens, "
            "design_plan_estimated_cost = :design_plan_estimated_cost, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeValues={
            ":design_plan_json": design_plan_json,
            ":design_plan_input_tokens": design_plan_input_tokens,
            ":design_plan_output_tokens": design_plan_output_tokens,
            ":design_plan_estimated_cost": design_plan_estimated_cost,
            ":updated_at": updated_at,
        },
    )


def save_landing_page_result(item: dict[str, Any]) -> None:
    get_result_table().put_item(
        Item=_to_dynamodb_safe_item(item),
        ConditionExpression="attribute_not_exists(result_id)",
    )


def get_landing_page_result(result_id: str) -> dict[str, Any] | None:
    response = get_result_table().get_item(
        Key={
            "result_id": result_id,
        }
    )
    return response.get("Item")


def save_landing_page_result_reference(
    request_id: str,
    *,
    landing_result_id: str,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={
            "request_id": request_id,
        },
        UpdateExpression=(
            "SET landing_result_id = :landing_result_id, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeValues={
            ":landing_result_id": landing_result_id,
            ":updated_at": updated_at,
        },
    )


def save_landing_page_variant_selection(
    request_id: str,
    *,
    chosen_variant: str,
    selected_design_plan_id: str,
    design_plan_vector_store: str,
    design_plan_vector_bucket: str,
    design_plan_vector_index: str,
    design_plan_vector_key: str,
    design_plan_vector_uri: str,
    selected_at: str,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={
            "request_id": request_id,
        },
        UpdateExpression=(
            "SET selection_status = :selection_status, "
            "chosen_variant = :chosen_variant, "
            "selected_design_plan_id = :selected_design_plan_id, "
            "design_plan_vector_store = :design_plan_vector_store, "
            "design_plan_vector_bucket = :design_plan_vector_bucket, "
            "design_plan_vector_index = :design_plan_vector_index, "
            "design_plan_vector_key = :design_plan_vector_key, "
            "design_plan_vector_uri = :design_plan_vector_uri, "
            "selected_at = :selected_at, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeValues={
            ":selection_status": "SELECTED",
            ":chosen_variant": chosen_variant,
            ":selected_design_plan_id": selected_design_plan_id,
            ":design_plan_vector_store": design_plan_vector_store,
            ":design_plan_vector_bucket": design_plan_vector_bucket,
            ":design_plan_vector_index": design_plan_vector_index,
            ":design_plan_vector_key": design_plan_vector_key,
            ":design_plan_vector_uri": design_plan_vector_uri,
            ":selected_at": selected_at,
            ":updated_at": updated_at,
        },
    )
