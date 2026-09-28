from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

from app.config.settings import settings
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
        },
        ConsistentRead=True,
    )
    return response.get("Item")


def list_landing_page_requests_by_user(user_id: str) -> list[dict[str, Any]]:
    table = get_request_table()
    query_kwargs: dict[str, Any] = {
        "IndexName": settings.LANDING_REQUEST_USER_INDEX_NAME,
        "KeyConditionExpression": Key("user_id").eq(user_id),
        "ScanIndexForward": False,
        "ProjectionExpression": (
            "request_id, project_id, industry, sub_industry, target, #style, goal, "
            "#status, current_step, progress, selection_status, chosen_variant, "
            "created_at, updated_at"
        ),
        "ExpressionAttributeNames": {
            "#status": "status",
            "#style": "style"},
    }
    items: list[dict[str, Any]] = []

    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    return items


def update_landing_page_request_state(
    request_id: str,
    *,
    status: str | None = None,
    current_step: str | None = None,
    progress: int | None = None,
    error_message: str | None = None,
    error_type: str | None = None,
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
    if error_type is not None:
        update_parts.append("error_type = :error_type")
        expression_values[":error_type"] = error_type

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


def save_landing_page_workflow_execution(
    request_id: str,
    *,
    execution_arn: str,
    start_date: str,
    status: str,
    progress: int,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={
            "request_id": request_id,
        },
        UpdateExpression=(
            "SET workflow_execution_arn = :workflow_execution_arn, "
            "workflow_start_date = :workflow_start_date, "
            "#status = :status, "
            "progress = :progress, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":workflow_execution_arn": execution_arn,
            ":workflow_start_date": start_date,
            ":status": status,
            ":progress": progress,
            ":updated_at": updated_at,
        },
    )


def mark_landing_page_request_failed(
    request_id: str,
    *,
    current_step: str,
    error_type: str,
    error_message: str,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={
            "request_id": request_id,
        },
        UpdateExpression=(
            "SET #status = :status, "
            "current_step = :current_step, "
            "error_type = :error_type, "
            "error_message = :error_message, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":status": "FAILED",
            ":current_step": current_step,
            ":error_type": error_type,
            ":error_message": error_message,
            ":updated_at": updated_at,
        },
    )


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


def save_landing_page_revision(item: dict[str, Any]) -> None:
    get_result_table().put_item(
        Item=_to_dynamodb_safe_item(item),
        ConditionExpression="attribute_not_exists(result_id)",
    )


def get_landing_page_revision(revision_id: str) -> dict[str, Any] | None:
    item = get_landing_page_result(revision_id)
    if item is None or item.get("item_type") != "REVISION":
        return None
    return item


def list_landing_page_revisions(request_id: str) -> list[dict[str, Any]]:
    table = get_result_table()
    query_kwargs: dict[str, Any] = {
        "IndexName": "RequestIdIndex",
        "KeyConditionExpression": Key("request_id").eq(request_id),
        "FilterExpression": "item_type = :item_type",
        "ExpressionAttributeValues": {":item_type": "REVISION"},
    }
    items: list[dict[str, Any]] = []
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key
    return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)


def mark_landing_page_revision_failed(
    revision_id: str,
    *,
    error_type: str,
    error_message: str,
    updated_at: str,
) -> None:
    get_result_table().update_item(
        Key={"result_id": revision_id},
        UpdateExpression=(
            "SET #status = :failed, error_type = :error_type, "
            "error_message = :error_message, updated_at = :updated_at"
        ),
        ConditionExpression=(
            "item_type = :item_type AND "
            "(#status = :queued OR #status = :processing)"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":failed": "FAILED",
            ":processing": "PROCESSING",
            ":queued": "QUEUED",
            ":item_type": "REVISION",
            ":error_type": error_type,
            ":error_message": error_message,
            ":updated_at": updated_at,
        },
    )


def mark_landing_page_revision_processing(
    revision_id: str,
    *,
    processing_started_at: str,
    updated_at: str,
) -> None:
    get_result_table().update_item(
        Key={"result_id": revision_id},
        UpdateExpression=(
            "SET #status = :processing, "
            "processing_started_at = :processing_started_at, "
            "updated_at = :updated_at"
        ),
        ConditionExpression="item_type = :item_type AND #status = :queued",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":queued": "QUEUED",
            ":processing": "PROCESSING",
            ":item_type": "REVISION",
            ":processing_started_at": processing_started_at,
            ":updated_at": updated_at,
        },
    )


def complete_landing_page_revision(
    request_id: str,
    revision_id: str,
    *,
    title: str,
    html_s3_bucket: str,
    html_s3_key: str,
    input_tokens: int,
    output_tokens: int,
    updated_at: str,
) -> None:
    def serialize_map(value: dict[str, Any]) -> dict[str, Any]:
        # Table.meta.client inherits the DynamoDB resource's attribute-value
        # transformer, so it expects native Python values here. Pre-serializing
        # with TypeSerializer would serialize them a second time.
        return _to_dynamodb_safe_item(value)

    request_table = get_request_table()
    result_table = get_result_table()
    request_table.meta.client.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": result_table.name,
                    "Key": serialize_map({"result_id": revision_id}),
                    "UpdateExpression": (
                        "SET #status = :completed, title = :title, "
                        "html_s3_bucket = :html_s3_bucket, "
                        "html_s3_key = :html_s3_key, "
                        "revision_input_tokens = :input_tokens, "
                        "revision_output_tokens = :output_tokens, "
                        "updated_at = :updated_at"
                    ),
                    "ConditionExpression": (
                        "item_type = :item_type AND #status = :processing"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": serialize_map(
                        {
                            ":completed": "COMPLETED",
                            ":processing": "PROCESSING",
                            ":item_type": "REVISION",
                            ":title": title,
                            ":html_s3_bucket": html_s3_bucket,
                            ":html_s3_key": html_s3_key,
                            ":input_tokens": input_tokens,
                            ":output_tokens": output_tokens,
                            ":updated_at": updated_at,
                        }
                    ),
                }
            },
            {
                "Update": {
                    "TableName": request_table.name,
                    "Key": serialize_map({"request_id": request_id}),
                    "UpdateExpression": (
                        "SET latest_revision_id = :revision_id, "
                        "updated_at = :updated_at"
                    ),
                    "ConditionExpression": "attribute_exists(request_id)",
                    "ExpressionAttributeValues": serialize_map(
                        {
                            ":revision_id": revision_id,
                            ":updated_at": updated_at,
                        }
                    ),
                }
            },
        ]
    )


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


def save_initial_revision_and_landing_page_variant_selection(
    request_id: str,
    *,
    revision_item: dict[str, Any],
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
    def serialize_map(value: dict[str, Any]) -> dict[str, Any]:
        # Table.meta.client automatically converts native Python values to
        # DynamoDB AttributeValue objects for resource-originated clients.
        return _to_dynamodb_safe_item(value)

    revision_id = revision_item["revision_id"]
    request_table = get_request_table()
    result_table = get_result_table()
    transaction_client = request_table.meta.client
    transaction_client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": result_table.name,
                    "Item": serialize_map(revision_item),
                    "ConditionExpression": "attribute_not_exists(result_id)",
                }
            },
            {
                "Update": {
                    "TableName": request_table.name,
                    "Key": serialize_map({"request_id": request_id}),
                    "UpdateExpression": (
                        "SET selection_status = :selection_status, "
                        "chosen_variant = :chosen_variant, "
                        "selected_design_plan_id = :selected_design_plan_id, "
                        "design_plan_vector_store = :design_plan_vector_store, "
                        "design_plan_vector_bucket = :design_plan_vector_bucket, "
                        "design_plan_vector_index = :design_plan_vector_index, "
                        "design_plan_vector_key = :design_plan_vector_key, "
                        "design_plan_vector_uri = :design_plan_vector_uri, "
                        "latest_revision_id = :latest_revision_id, "
                        "selected_at = :selected_at, "
                        "updated_at = :updated_at"
                    ),
                    "ConditionExpression": (
                        "attribute_exists(request_id) AND "
                        "(attribute_not_exists(selection_status) OR "
                        "selection_status <> :selected)"
                    ),
                    "ExpressionAttributeValues": serialize_map(
                        {
                            ":selection_status": "SELECTED",
                            ":selected": "SELECTED",
                            ":chosen_variant": chosen_variant,
                            ":selected_design_plan_id": selected_design_plan_id,
                            ":design_plan_vector_store": design_plan_vector_store,
                            ":design_plan_vector_bucket": design_plan_vector_bucket,
                            ":design_plan_vector_index": design_plan_vector_index,
                            ":design_plan_vector_key": design_plan_vector_key,
                            ":design_plan_vector_uri": design_plan_vector_uri,
                            ":latest_revision_id": revision_id,
                            ":selected_at": selected_at,
                            ":updated_at": updated_at,
                        }
                    ),
                }
            },
        ]
    )
