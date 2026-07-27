import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
from botocore.exceptions import UnknownServiceError

from app.config.settings import settings


def _use_local_vector_storage() -> bool:
    return (
        settings.APP_ENV == "local"
        and not settings.S3_VECTOR_BUCKET_NAME
        and not settings.S3_VECTOR_INDEX_NAME
    )


def _get_s3_vectors_client():
    client_kwargs = {
        "region_name": settings.AWS_DEFAULT_REGION or settings.REGION_NAME,
    }

    if settings.S3_VECTOR_ENDPOINT_URL:
        client_kwargs.update(
            {
                "endpoint_url": settings.S3_VECTOR_ENDPOINT_URL,
                "aws_access_key_id": "dummy",
                "aws_secret_access_key": "dummy",
                "aws_session_token": None,
            }
        )

    try:
        return boto3.client("s3vectors", **client_kwargs)
    except UnknownServiceError as err:
        raise ValueError(
            "현재 boto3/botocore 버전은 s3vectors 클라이언트를 지원하지 않습니다. "
            "requirements.txt의 boto3 버전을 업그레이드해 주세요."
        ) from err


def build_design_plan_vector_key(
    *,
    request_id: str,
    variant: str,
) -> str:
    safe_request_id = quote(request_id, safe="-_.~")
    safe_variant = quote(variant, safe="-_.~")
    return f"rag_{safe_request_id}_{safe_variant}"


def build_design_plan_vector_index(*, industry: str | None) -> str:
    if settings.S3_VECTOR_INDEX_NAME:
        return settings.S3_VECTOR_INDEX_NAME
    if not industry:
        raise ValueError("industry is required to resolve S3 Vector index")
    return f"{industry.replace('_', '-')}-index"


def _to_metadata_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    return value


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _to_metadata_safe_value(value)
        for key, value in metadata.items()
        if value is not None
    }


def _write_design_plan_vector_to_local_file(
    *,
    key: str,
    vector_payload: dict[str, Any],
) -> dict[str, str]:
    local_path = Path(settings.LOCAL_STORAGE_DIR) / "s3-vectors" / f"{key}.json"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(
        json.dumps(vector_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "design_plan_vector_store": "local",
        "design_plan_vector_bucket": "local",
        "design_plan_vector_index": "local",
        "design_plan_vector_key": key,
        "design_plan_vector_uri": local_path.resolve().as_uri(),
    }


def save_design_plan_vector(
    *,
    request_id: str,
    variant: str,
    industry: str | None,
    embedding: list[float],
    metadata: dict[str, Any],
) -> dict[str, str]:
    key = build_design_plan_vector_key(
        request_id=request_id,
        variant=variant,
    )
    index_name = build_design_plan_vector_index(industry=industry)
    vector_payload = {
        "key": key,
        "data": {
            "float32": embedding,
        },
        "metadata": _compact_metadata(metadata),
    }

    if _use_local_vector_storage():
        return _write_design_plan_vector_to_local_file(
            key=key,
            vector_payload=vector_payload,
        )

    if not settings.S3_VECTOR_BUCKET_NAME:
        raise ValueError("S3_VECTOR_BUCKET_NAME is required when using S3 Vectors")

    _get_s3_vectors_client().put_vectors(
        vectorBucketName=settings.S3_VECTOR_BUCKET_NAME,
        indexName=index_name,
        vectors=[vector_payload],
    )

    return {
        "design_plan_vector_store": "s3vectors",
        "design_plan_vector_bucket": settings.S3_VECTOR_BUCKET_NAME,
        "design_plan_vector_index": index_name,
        "design_plan_vector_key": key,
        "design_plan_vector_uri": (
            f"s3vectors://{settings.S3_VECTOR_BUCKET_NAME}/"
            f"{index_name}/{key}"
        ),
    }


def query_nearest_design_plan_request_ids(
    *,
    industry: str | None,
    embedding: list[float],
    top_k: int = 10,
    exclude_request_id: str | None = None,
) -> list[str]:
    index_name = build_design_plan_vector_index(industry=industry)

    if _use_local_vector_storage() or not settings.S3_VECTOR_BUCKET_NAME:
        return []

    response = _get_s3_vectors_client().query_vectors(
        vectorBucketName=settings.S3_VECTOR_BUCKET_NAME,
        indexName=index_name,
        topK=top_k,
        queryVector={
            "float32": embedding,
        },
        returnMetadata=True,
        returnDistance=True,
    )

    request_ids: list[str] = []
    seen_request_ids: set[str] = set()
    for vector in response.get("vectors", []):
        metadata = vector.get("metadata") or {}
        request_id = metadata.get("request_id")
        if not request_id or request_id == exclude_request_id:
            continue
        if request_id in seen_request_ids:
            continue
        seen_request_ids.add(request_id)
        request_ids.append(request_id)

    return request_ids
