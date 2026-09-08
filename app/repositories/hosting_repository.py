from typing import Any

from botocore.exceptions import ClientError

from app.repositories.dynamodb_setup import get_core_table, get_request_table


def reserve_site_slug(item: dict[str, Any]) -> bool:
    try:
        get_core_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def get_site_reservation(site_slug: str) -> dict[str, Any] | None:
    response = get_core_table().get_item(
        Key={"PK": f"HOSTING_SITE#{site_slug}", "SK": "METADATA"}
    )
    return response.get("Item")


def start_hosting_publish(
    request_id: str,
    *,
    site_id: str,
    project_name: str,
    site_slug: str,
    published_url: str,
    operation_id: str,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={"request_id": request_id},
        UpdateExpression=(
            "SET hosting_status = :publishing, site_id = :site_id, "
            "site_name = :site_name, site_slug = :site_slug, "
            "published_url = :published_url, publish_operation_id = :operation_id, "
            "hosting_updated_at = :updated_at, updated_at = :updated_at "
            "REMOVE hosting_error_type, hosting_error_message"
        ),
        ConditionExpression=(
            "attribute_exists(request_id) AND "
            "(attribute_not_exists(hosting_status) OR hosting_status <> :publishing)"
        ),
        ExpressionAttributeValues={
            ":publishing": "PUBLISHING",
            ":site_id": site_id,
            ":site_name": project_name,
            ":site_slug": site_slug,
            ":published_url": published_url,
            ":operation_id": operation_id,
            ":updated_at": updated_at,
        },
    )


def complete_hosting_publish(
    request_id: str,
    *,
    operation_id: str,
    revision_id: str,
    published_at: str,
) -> None:
    get_request_table().update_item(
        Key={"request_id": request_id},
        UpdateExpression=(
            "SET hosting_status = :published, "
            "published_revision_id = :revision_id, "
            "published_at = :published_at, hosting_updated_at = :published_at, "
            "updated_at = :published_at "
            "REMOVE publish_operation_id, hosting_error_type, hosting_error_message"
        ),
        ConditionExpression="publish_operation_id = :operation_id",
        ExpressionAttributeValues={
            ":published": "PUBLISHED",
            ":revision_id": revision_id,
            ":published_at": published_at,
            ":operation_id": operation_id,
        },
    )


def fail_hosting_publish(
    request_id: str,
    *,
    operation_id: str,
    fallback_status: str,
    error_type: str,
    error_message: str,
    updated_at: str,
) -> None:
    get_request_table().update_item(
        Key={"request_id": request_id},
        UpdateExpression=(
            "SET hosting_status = :fallback_status, "
            "hosting_error_type = :error_type, hosting_error_message = :error_message, "
            "hosting_updated_at = :updated_at, updated_at = :updated_at "
            "REMOVE publish_operation_id"
        ),
        ConditionExpression="publish_operation_id = :operation_id",
        ExpressionAttributeValues={
            ":fallback_status": fallback_status,
            ":error_type": error_type,
            ":error_message": error_message,
            ":updated_at": updated_at,
            ":operation_id": operation_id,
        },
    )


def mark_hosting_unpublished(request_id: str, *, updated_at: str) -> None:
    get_request_table().update_item(
        Key={"request_id": request_id},
        UpdateExpression=(
            "SET hosting_status = :unpublished, hosting_updated_at = :updated_at, "
            "updated_at = :updated_at "
            "REMOVE published_revision_id, published_at, publish_operation_id, "
            "hosting_error_type, hosting_error_message"
        ),
        ConditionExpression=(
            "attribute_exists(request_id) AND "
            "(attribute_not_exists(hosting_status) OR hosting_status <> :publishing)"
        ),
        ExpressionAttributeValues={
            ":unpublished": "UNPUBLISHED",
            ":publishing": "PUBLISHING",
            ":updated_at": updated_at,
        },
    )
