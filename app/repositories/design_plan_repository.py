from decimal import Decimal
from typing import Any

from app.repositories.dynamodb_setup import get_request_table, get_result_table


def save_design_plan_request(item: dict[str, Any]) -> None:
    get_request_table().put_item(Item=item)


def save_design_plan_result(item: dict[str, Any]) -> None:
    repository_item = {
        **item,
        "estimated_cost": Decimal(str(item["estimated_cost"])),
    }
    get_result_table().put_item(Item=repository_item)
