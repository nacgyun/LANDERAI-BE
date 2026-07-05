from typing import Any

from app.repositories.dynamodb_setup import get_core_table


def signup_user_profile(item: dict[str, Any]) -> None:
    get_core_table().put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    response = get_core_table().get_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": "PROFILE",
        }
    )
    return response.get("Item")


def soft_delete_user_profile(user_id: str, deleted_at: str) -> None:
    get_core_table().update_item(
        Key={
            "PK": f"USER#{user_id}",
            "SK": "PROFILE",
        },
        UpdateExpression="SET deleted_at = :deleted_at, updated_at = :updated_at",
        ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
        ExpressionAttributeValues={
            ":deleted_at": deleted_at,
            ":updated_at": deleted_at,
        },
    )
