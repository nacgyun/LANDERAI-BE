import boto3
from botocore.config import Config
from pathlib import Path
from urllib.parse import quote

from app.config.settings import settings


def _use_local_storage() -> bool:
    return (
        settings.APP_ENV == "local"
        and not settings.S3_BUCKET_NAME
        and not settings.S3_ENDPOINT_URL
    )


def _get_s3_client():
    client_kwargs = {
        "region_name": settings.AWS_DEFAULT_REGION or settings.REGION_NAME,
        "config": Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    }

    if settings.S3_ENDPOINT_URL:
        client_kwargs.update(
            {
                "endpoint_url": settings.S3_ENDPOINT_URL,
                "aws_access_key_id": "dummy",
                "aws_secret_access_key": "dummy",
                "aws_session_token": None,
                "config": Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            }
        )

    return boto3.client("s3", **client_kwargs)


def build_landing_page_variant_key(
    *,
    user_id: str,
    request_id: str,
    variant: str,
) -> str:
    safe_user_id = quote(user_id, safe="-_.~")
    safe_request_id = quote(request_id, safe="-_.~")
    safe_variant = quote(variant, safe="-_.~")
    return (
        f"landing-pages/{safe_user_id}/{safe_request_id}/"
        f"variants/{safe_variant}/index.html"
    )


def _write_landing_page_html_to_local_file(*, key: str, html: str) -> dict:
    local_path = Path(settings.LOCAL_STORAGE_DIR) / key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(html, encoding="utf-8")

    html_url = local_path.resolve().as_uri()
    return {
        "html_s3_bucket": "local",
        "html_s3_key": key,
        "html_s3_uri": html_url,
        "html_url": html_url,
    }


def upload_landing_page_html(
    *,
    user_id: str,
    request_id: str,
    variant: str,
    html: str,
) -> dict:
    key = build_landing_page_variant_key(
        user_id=user_id,
        request_id=request_id,
        variant=variant,
    )

    if _use_local_storage():
        return _write_landing_page_html_to_local_file(key=key, html=html)

    if not settings.S3_BUCKET_NAME:
        raise ValueError("S3_BUCKET_NAME is required when using S3 storage")

    _get_s3_client().put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=key,
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-cache",
    )

    s3_uri = f"s3://{settings.S3_BUCKET_NAME}/{key}"
    html_url = s3_uri
    if settings.S3_PUBLIC_BASE_URL:
        html_url = f"{settings.S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"

    return {
        "html_s3_bucket": settings.S3_BUCKET_NAME,
        "html_s3_key": key,
        "html_s3_uri": s3_uri,
        "html_url": html_url,
    }


def create_landing_page_preview_url(
    *,
    bucket: str,
    key: str,
    expires_in: int,
) -> str:
    if bucket == "local":
        local_path = Path(settings.LOCAL_STORAGE_DIR) / key
        return local_path.resolve().as_uri()

    return _get_s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
        },
        ExpiresIn=expires_in,
    )


def create_landing_page_download_url(
    *,
    bucket: str,
    key: str,
    filename: str,
    expires_in: int,
) -> str:
    if bucket == "local":
        local_path = Path(settings.LOCAL_STORAGE_DIR) / key
        return local_path.resolve().as_uri()

    return _get_s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentType": "text/html; charset=utf-8",
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expires_in,
    )
