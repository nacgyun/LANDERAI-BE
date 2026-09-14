import re
import logging
import unicodedata
import uuid
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.config.settings import settings
from app.repositories.hosting_repository import (
    complete_hosting_publish,
    fail_hosting_publish,
    get_request_site,
    mark_hosting_unpublished,
    reserve_site_slug,
    start_hosting_publish,
    start_hosting_unpublish,
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
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_accessible_request(request_id: str, current_user: dict) -> dict:
    try:
        request_item = get_landing_page_request(request_id)
    except (BotoCoreError, ClientError) as err:
        logger.exception("Hosting request lookup failed request_id=%s", request_id)
        raise HTTPException(status_code=503, detail="호스팅 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") from err
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
        assigned = get_request_site(request_id)
        if assigned.get("site_slug"):
            return assigned["site_slug"]
        suffix = "" if attempt == 0 else f"-{uuid.uuid4().hex[:SITE_SLUG_RANDOM_LENGTH]}"
        site_slug = f"{base_slug[:SITE_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"
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
    assigned = get_request_site(request_id)
    if assigned.get("site_slug"):
        return assigned["site_slug"]
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
        "error_message": (
            "호스팅 작업을 완료하지 못했습니다. 상태를 확인하고 다시 시도해 주세요."
            if request_item.get("hosting_error_message") else None
        ),
    }


def publish_revision(
    request_id: str,
    revision_id: str,
    publish_request: LandingPagePublishRequest,
    current_user: dict,
) -> dict:
    request_item = _get_accessible_request(request_id, current_user)
    if request_item.get("hosting_status") in {"PUBLISHING", "UNPUBLISHING"}:
        raise HTTPException(status_code=409, detail="다른 호스팅 작업이 진행 중입니다. 완료 후 다시 시도해 주세요.")
    if request_item.get("hosting_status") == "UNPUBLISH_FAILED":
        raise HTTPException(status_code=409, detail="게시 중단을 먼저 완료한 뒤 다시 게시해 주세요.")
    try:
        revision = get_landing_page_revision(revision_id)
    except (BotoCoreError, ClientError) as err:
        logger.exception("Hosting revision lookup failed request_id=%s", request_id)
        raise HTTPException(status_code=503, detail="게시할 버전을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") from err
    if revision is None or revision.get("request_id") != request_id:
        raise HTTPException(status_code=404, detail="게시할 버전을 찾을 수 없습니다.")
    if revision.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail="완료된 버전만 게시할 수 있습니다.")
    source_bucket = revision.get("html_s3_bucket")
    source_key = revision.get("html_s3_key")
    if not source_bucket or not source_key:
        raise HTTPException(status_code=409, detail="버전의 HTML 저장 정보가 없습니다.")
    project_name = request_item.get("site_name") or publish_request.project_name
    if not project_name:
        raise HTTPException(
            status_code=422,
            detail="최초 호스팅에는 프로젝트 이름이 필요합니다.",
        )
    if not request_item.get("site_slug"):
        normalize_site_slug(project_name)
    if not settings.HOSTING_BASE_DOMAIN:
        raise HTTPException(status_code=503, detail="호스팅 도메인이 설정되지 않았습니다.")
    if settings.APP_ENV != "local" and not settings.HOSTING_S3_BUCKET_NAME:
        raise HTTPException(status_code=503, detail="호스팅 S3 버킷이 설정되지 않았습니다.")

    now = _now()
    try:
        site_slug = request_item.get("site_slug") or _reserve_slug(
            request_id=request_id,
            user_id=request_item["user_id"],
            project_name=project_name,
            created_at=now,
        )
        assigned = get_request_site(request_id)
        project_name = assigned.get("site_name") or project_name
    except (BotoCoreError, ClientError) as err:
        logger.exception("Hosting address reservation failed request_id=%s", request_id)
        raise HTTPException(
            status_code=503,
            detail="사이트 주소를 확보하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ) from err
    site_id = request_item.get("site_id") or f"site_{uuid.uuid4().hex}"
    published_url = _build_published_url(site_slug)
    operation_id = f"publish_{uuid.uuid4().hex}"
    fallback_status = "PUBLISHED" if request_item.get("hosting_status") == "PUBLISHED" else "FAILED"
    published_object_written = False
    operation_started = False

    try:
        try:
            request_item = start_hosting_publish(
                request_id,
                site_id=site_id,
                project_name=project_name,
                site_slug=site_slug,
                published_url=published_url,
                operation_id=operation_id,
                updated_at=now,
            )
            operation_started = True
            # Use the state at lock acquisition, not a read from before an unpublish.
            fallback_status = "PUBLISHED" if request_item.get("hosting_status") == "PUBLISHED" else "FAILED"
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise HTTPException(
                    status_code=409,
                    detail="호스팅 작업이 진행 중이거나 게시 중단을 완료해야 합니다. 상태를 확인해 주세요.",
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
        logger.exception("Hosting publish failed request_id=%s", request_id)
        # A transport timeout does not prove that S3 rejected the write.
        if isinstance(err, BotoCoreError):
            fallback_status = "FAILED"
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
                    fallback_status = "FAILED"
            except (BotoCoreError, ClientError, OSError, ValueError) as restore_err:
                fallback_status = "FAILED"
                print(
                    "[Hosting] failed to restore published object "
                    f"request_id={request_id} site_slug={site_slug} error={restore_err}"
                )
        if operation_started:
            try:
                fail_hosting_publish(
                    request_id,
                    operation_id=operation_id,
                    fallback_status=fallback_status,
                    error_type="PUBLISH_FAILED",
                    error_message="사이트 게시를 완료하지 못했습니다. 상태를 확인하고 다시 시도해 주세요.",
                    updated_at=_now(),
                )
            except (BotoCoreError, ClientError):
                logger.exception("Failed to record publish failure request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="사이트 게시를 완료하지 못했습니다. 상태를 확인하고 다시 시도해 주세요.",
        ) from err


def get_hosting(request_id: str, current_user: dict) -> dict:
    try:
        return _hosting_response(_get_accessible_request(request_id, current_user))
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as err:
        logger.exception("Hosting status lookup failed request_id=%s", request_id)
        raise HTTPException(status_code=503, detail="호스팅 상태를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") from err


def unpublish(request_id: str, current_user: dict) -> dict:
    operation_id = f"unpublish_{uuid.uuid4().hex}"
    operation_started = False
    try:
        _get_accessible_request(request_id, current_user)
        try:
            request_item = start_hosting_unpublish(
                request_id, operation_id=operation_id, updated_at=_now(),
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise HTTPException(
                    status_code=409, detail="다른 호스팅 작업이 진행 중이거나 요청이 변경되었습니다. 다시 확인해 주세요.",
                ) from err
            raise
        operation_started = True
        # Delete even without a revision pointer: a prior partial failure may have left HTML.
        site_slug = request_item.get("site_slug")
        if site_slug:
            delete_published_landing_page(site_slug=site_slug)
        updated_at = _now()
        mark_hosting_unpublished(request_id, operation_id=operation_id, updated_at=updated_at)
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
        logger.exception("Hosting unpublish failed request_id=%s", request_id)
        if operation_started:
            try:
                fail_hosting_publish(
                    request_id, operation_id=operation_id,
                    fallback_status="UNPUBLISH_FAILED",
                    error_type="UNPUBLISH_FAILED",
                    error_message="게시 중단을 완료하지 못했습니다. 게시 중단을 다시 시도해 주세요.",
                    updated_at=_now(),
                )
            except (BotoCoreError, ClientError):
                logger.exception("Failed to record unpublish failure request_id=%s", request_id)
        raise HTTPException(
            status_code=503,
            detail="게시 중단을 완료하지 못했습니다. 상태를 확인하고 다시 시도해 주세요.",
        ) from err
