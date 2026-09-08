import re
import unicodedata
import uuid
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.config.settings import settings
from app.repositories.hosting_repository import (
    complete_hosting_publish,
    fail_hosting_publish,
    get_site_reservation,
    mark_hosting_unpublished,
    reserve_site_slug,
    start_hosting_publish,
)
from app.repositories.request_repository import (
    get_landing_page_request,
    get_landing_page_revision,
)
from app.repositories.s3_repository import (
    delete_published_landing_page,
    publish_landing_page_html,
)
from app.schemas.hosting import LandingPagePublishRequest


RESERVED_SITE_SLUGS = {
    "admin", "api", "app", "accounts", "clerk", "dev", "static", "www"
}
SITE_SLUG_MAX_LENGTH = 50
SITE_SLUG_RANDOM_LENGTH = 6
SITE_SLUG_RESERVATION_ATTEMPTS = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_accessible_request(request_id: str, current_user: dict) -> dict:
    request_item = get_landing_page_request(request_id)
    if request_item is None:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다.")
    if (
        current_user.get("role") != "ADMIN"
        and request_item.get("user_id") != current_user["user_id"]
    ):
        raise HTTPException(status_code=403, detail="이 요청에 접근할 권한이 없습니다.")
    return request_item


def normalize_site_slug(project_name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", project_name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    slug = slug[:SITE_SLUG_MAX_LENGTH].rstrip("-")
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="프로젝트 이름에 영문 또는 숫자를 하나 이상 포함해 주세요.",
        )
    if slug in RESERVED_SITE_SLUGS:
        slug = f"{slug}-site"
    return slug


def _build_published_url(site_slug: str) -> str:
    if not settings.HOSTING_BASE_DOMAIN:
        raise ValueError("HOSTING_BASE_DOMAIN is required for hosting")
    return f"https://{site_slug}.{settings.HOSTING_BASE_DOMAIN.strip('.')}"


def _reserve_slug(
    *, request_id: str, user_id: str, project_name: str, created_at: str
) -> str:
    base_slug = normalize_site_slug(project_name)
    for attempt in range(SITE_SLUG_RESERVATION_ATTEMPTS):
        suffix = "" if attempt == 0 else f"-{uuid.uuid4().hex[:SITE_SLUG_RANDOM_LENGTH]}"
        site_slug = f"{base_slug[:SITE_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"
        existing = get_site_reservation(site_slug)
        if existing and existing.get("request_id") == request_id:
            return site_slug
        reservation = {
            "PK": f"HOSTING_SITE#{site_slug}",
            "SK": "METADATA",
            "item_type": "HOSTING_SITE",
            "site_slug": site_slug,
            "request_id": request_id,
            "user_id": user_id,
            "project_name": project_name,
            "created_at": created_at,
        }
        if reserve_site_slug(reservation):
            return site_slug
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="사용 가능한 사이트 주소를 만들지 못했습니다. 다른 이름을 입력해 주세요.",
    )


def _hosting_response(request_item: dict) -> dict:
    return {
        "request_id": request_item["request_id"],
        "site_id": request_item.get("site_id"),
        "project_name": request_item.get("site_name"),
        "site_slug": request_item.get("site_slug"),
        "hosting_status": request_item.get("hosting_status", "UNPUBLISHED"),
        "published_revision_id": request_item.get("published_revision_id"),
        "published_url": request_item.get("published_url"),
        "published_at": request_item.get("published_at"),
        "error_type": request_item.get("hosting_error_type"),
        "error_message": request_item.get("hosting_error_message"),
    }


def publish_revision(
    request_id: str,
    revision_id: str,
    publish_request: LandingPagePublishRequest,
    current_user: dict,
) -> dict:
    request_item = _get_accessible_request(request_id, current_user)
    revision = get_landing_page_revision(revision_id)
    if revision is None or revision.get("request_id") != request_id:
        raise HTTPException(status_code=404, detail="게시할 버전을 찾을 수 없습니다.")
    if revision.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail="완료된 버전만 게시할 수 있습니다.")
    source_bucket = revision.get("html_s3_bucket")
    source_key = revision.get("html_s3_key")
    if not source_bucket or not source_key:
        raise HTTPException(status_code=409, detail="버전의 HTML 저장 정보가 없습니다.")
    if not settings.HOSTING_BASE_DOMAIN:
        raise HTTPException(status_code=503, detail="호스팅 도메인이 설정되지 않았습니다.")
    if settings.APP_ENV != "local" and not settings.HOSTING_S3_BUCKET_NAME:
        raise HTTPException(status_code=503, detail="호스팅 S3 버킷이 설정되지 않았습니다.")

    project_name = request_item.get("site_name") or publish_request.project_name
    if not project_name:
        raise HTTPException(
            status_code=422,
            detail="최초 호스팅에는 프로젝트 이름이 필요합니다.",
        )

    now = _now()
    site_slug = request_item.get("site_slug") or _reserve_slug(
        request_id=request_id,
        user_id=request_item["user_id"],
        project_name=project_name,
        created_at=now,
    )
    site_id = request_item.get("site_id") or f"site_{uuid.uuid4().hex}"
    published_url = _build_published_url(site_slug)
    operation_id = f"publish_{uuid.uuid4().hex}"
    fallback_status = "PUBLISHED" if request_item.get("published_revision_id") else "FAILED"
    published_object_written = False

    try:
        try:
            start_hosting_publish(
                request_id,
                site_id=site_id,
                project_name=project_name,
                site_slug=site_slug,
                published_url=published_url,
                operation_id=operation_id,
                updated_at=now,
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise HTTPException(
                    status_code=409,
                    detail="다른 게시 작업이 진행 중입니다.",
                ) from err
            raise
        publish_landing_page_html(
            source_bucket=source_bucket,
            source_key=source_key,
            site_slug=site_slug,
        )
        published_object_written = True
        published_at = _now()
        complete_hosting_publish(
            request_id,
            operation_id=operation_id,
            revision_id=revision_id,
            published_at=published_at,
        )
        return {
            "request_id": request_id,
            "site_id": site_id,
            "project_name": project_name,
            "site_slug": site_slug,
            "hosting_status": "PUBLISHED",
            "published_revision_id": revision_id,
            "published_url": published_url,
            "published_at": published_at,
            "error_type": None,
            "error_message": None,
        }
    except HTTPException:
        raise
    except (BotoCoreError, ClientError, OSError, ValueError) as err:
        if published_object_written:
            try:
                previous_revision_id = request_item.get("published_revision_id")
                previous_revision = (
                    get_landing_page_revision(previous_revision_id)
                    if previous_revision_id
                    else None
                )
                if (
                    previous_revision
                    and previous_revision.get("status") == "COMPLETED"
                    and previous_revision.get("html_s3_bucket")
                    and previous_revision.get("html_s3_key")
                ):
                    publish_landing_page_html(
                        source_bucket=previous_revision["html_s3_bucket"],
                        source_key=previous_revision["html_s3_key"],
                        site_slug=site_slug,
                    )
                else:
                    delete_published_landing_page(site_slug=site_slug)
            except (BotoCoreError, ClientError, OSError, ValueError) as restore_err:
                print(
                    "[Hosting] failed to restore published object "
                    f"request_id={request_id} site_slug={site_slug} error={restore_err}"
                )
        try:
            fail_hosting_publish(
                request_id,
                operation_id=operation_id,
                fallback_status=fallback_status,
                error_type=type(err).__name__,
                error_message=str(err),
                updated_at=_now(),
            )
        except (BotoCoreError, ClientError):
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"사이트 게시에 실패했습니다. 원인: {err}",
        ) from err


def get_hosting(request_id: str, current_user: dict) -> dict:
    try:
        return _hosting_response(_get_accessible_request(request_id, current_user))
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as err:
        raise HTTPException(status_code=503, detail=f"호스팅 상태 조회에 실패했습니다. 원인: {err}") from err


def unpublish(request_id: str, current_user: dict) -> dict:
    request_item = _get_accessible_request(request_id, current_user)
    if request_item.get("hosting_status") == "PUBLISHING":
        raise HTTPException(status_code=409, detail="게시 작업이 진행 중입니다.")
    site_slug = request_item.get("site_slug")
    if not site_slug or not request_item.get("published_revision_id"):
        return {**_hosting_response(request_item), "hosting_status": "UNPUBLISHED"}
    try:
        delete_published_landing_page(site_slug=site_slug)
        updated_at = _now()
        mark_hosting_unpublished(request_id, updated_at=updated_at)
        request_item.update(
            {
                "hosting_status": "UNPUBLISHED",
                "published_revision_id": None,
                "published_at": None,
                "hosting_error_type": None,
                "hosting_error_message": None,
            }
        )
        return _hosting_response(request_item)
    except (BotoCoreError, ClientError, OSError, ValueError) as err:
        raise HTTPException(
            status_code=503,
            detail=f"사이트 게시 중단에 실패했습니다. 원인: {err}",
        ) from err
