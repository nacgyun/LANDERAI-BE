from typing import Any

from app.repositories.dynamodb_setup import get_rag_design_plan_table
from app.repositories.request_repository import _to_dynamodb_safe_item


def save_rag_design_plan(item: dict[str, Any]) -> None:
    get_rag_design_plan_table().put_item(
        Item=_to_dynamodb_safe_item(item),
    )


def get_rag_design_plans_by_request_ids(
    request_ids: list[str],
) -> list[dict[str, Any]]:
    if not request_ids:
        return []

    items: list[dict[str, Any]] = []
    seen_design_plan_ids: set[str] = set()
    table = get_rag_design_plan_table()

    for request_id in request_ids:
        response = table.query(
            IndexName="RequestIdIndex",
            KeyConditionExpression="request_id = :request_id",
            ExpressionAttributeValues={
                ":request_id": request_id,
            },
        )
        for item in response.get("Items", []):
            design_plan_id = item.get("design_plan_id")
            if design_plan_id in seen_design_plan_ids:
                continue
            if design_plan_id:
                seen_design_plan_ids.add(design_plan_id)
            items.append(item)

    return items


def update_rag_design_plan_vector_reference(
    design_plan_id: str,
    *,
    vector_store: str,
    vector_bucket: str,
    vector_index: str,
    vector_key: str,
    vector_uri: str,
    embedding_model: str,
    embedding_input: str,
    embedding_input_tokens: int,
    updated_at: str,
) -> None:
    get_rag_design_plan_table().update_item(
        Key={
            "design_plan_id": design_plan_id,
        },
        UpdateExpression=(
            "SET vector_store = :vector_store, "
            "vector_bucket = :vector_bucket, "
            "vector_index = :vector_index, "
            "vector_key = :vector_key, "
            "vector_uri = :vector_uri, "
            "embedding_model = :embedding_model, "
            "embedding_input = :embedding_input, "
            "embedding_input_tokens = :embedding_input_tokens, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(design_plan_id)",
        ExpressionAttributeValues={
            ":vector_store": vector_store,
            ":vector_bucket": vector_bucket,
            ":vector_index": vector_index,
            ":vector_key": vector_key,
            ":vector_uri": vector_uri,
            ":embedding_model": embedding_model,
            ":embedding_input": embedding_input,
            ":embedding_input_tokens": embedding_input_tokens,
            ":updated_at": updated_at,
        },
    )
