import json

from app.config.settings import settings
from app.messaging.revision_events import REVISION_REQUESTED_EVENT
from app.services.revision_service import process_landing_page_revision


def _process_record(record: dict) -> None:
    message = json.loads(record.get("body") or "{}")
    if message.get("event_type") != REVISION_REQUESTED_EVENT:
        raise ValueError("지원하지 않는 Revision event_type입니다.")

    request_id = message.get("request_id")
    revision_id = message.get("revision_id")
    if not request_id or not revision_id:
        raise ValueError("request_id와 revision_id가 필요합니다.")

    receive_count = int(
        record.get("attributes", {}).get("ApproximateReceiveCount", "1")
    )
    process_landing_page_revision(
        request_id,
        revision_id,
        mark_failed_on_error=receive_count >= settings.REVISION_MAX_RECEIVE_COUNT,
    )


def lambda_handler(event, context):
    records = event.get("Records", [])
    if not records:
        raise ValueError("SQS Records가 필요합니다.")

    for record in records:
        _process_record(record)

    return {"processed": len(records)}
