import json

import boto3

from app.config.settings import settings
from app.messaging.revision_events import REVISION_REQUESTED_EVENT


def _get_sqs_client():
    client_kwargs = {
        "region_name": settings.AWS_DEFAULT_REGION or settings.REGION_NAME,
    }
    if settings.SQS_ENDPOINT_URL:
        client_kwargs.update(
            {
                "endpoint_url": settings.SQS_ENDPOINT_URL,
                "aws_access_key_id": "dummy",
                "aws_secret_access_key": "dummy",
                "aws_session_token": None,
            }
        )
    return boto3.client("sqs", **client_kwargs)


def publish_revision_requested(*, request_id: str, revision_id: str) -> str:
    if not settings.REVISION_QUEUE_URL:
        raise ValueError("REVISION_QUEUE_URL is required")

    response = _get_sqs_client().send_message(
        QueueUrl=settings.REVISION_QUEUE_URL,
        MessageBody=json.dumps(
            {
                "event_type": REVISION_REQUESTED_EVENT,
                "request_id": request_id,
                "revision_id": revision_id,
            }
        ),
        MessageAttributes={
            "event_type": {
                "DataType": "String",
                "StringValue": REVISION_REQUESTED_EVENT,
            }
        },
    )
    return response["MessageId"]
