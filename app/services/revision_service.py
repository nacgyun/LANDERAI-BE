import uuid
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.repositories.request_repository import (
    complete_landing_page_revision,
    get_landing_page_request,
    get_landing_page_revision,
    list_landing_page_revisions,
    mark_landing_page_revision_failed,
    mark_landing_page_revision_processing,
    save_landing_page_revision,
)
from app.messaging.revision_publisher import publish_revision_requested
from app.repositories.s3_repository import (
    get_landing_page_html,
    upload_landing_page_revision_html,
)
from app.schemas.revision import LandingPageRevision, LandingPageRevisionCreateRequest
from app.services.openai_service import revise_landing_page


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


def create_landing_page_revision(
    request_id: str,
    request: LandingPageRevisionCreateRequest,
    current_user: dict,
) -> dict:
    try:
        request_item = _get_accessible_request(request_id, current_user)
        if request_item.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="완료된 랜딩페이지에서만 수정할 수 있습니다.",
            )
        if request_item.get("selection_status") != "SELECTED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="수정할 A/B안을 먼저 선택해 주세요.",
            )

        source_revision = get_landing_page_revision(request.source_revision_id)
        if (
            source_revision is None
            or source_revision.get("request_id") != request_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="기준 Revision을 찾을 수 없습니다.",
            )
        if source_revision.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="완료된 Revision만 수정 기준으로 사용할 수 있습니다.",
            )

        revision_id = f"rev_{uuid.uuid4().hex}"
        now = _now()
        revision = LandingPageRevision(
            revision_id=revision_id,
            request_id=request_id,
            source_revision_id=request.source_revision_id,
            revision_prompt=request.revision_prompt,
            source_type="REVISION",
            created_at=now,
            updated_at=now,
            status="QUEUED",
        )
        save_landing_page_revision(
            {
                "result_id": revision_id,
                "item_type": "REVISION",
                **revision.model_dump(),
            }
        )
        return revision.model_dump()
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Revision 생성 중 저장소 요청에 실패했습니다. 원인: {err}",
        ) from err


def enqueue_revision(request_id: str, revision_id: str) -> str:
    try:
        return publish_revision_requested(
            request_id=request_id,
            revision_id=revision_id,
        )
    except Exception as err:
        try:
            mark_landing_page_revision_failed(
                revision_id,
                error_type=type(err).__name__,
                error_message=f"Revision 작업을 큐에 등록하지 못했습니다: {err}",
                updated_at=_now(),
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Revision 작업을 큐에 등록하지 못했습니다.",
        ) from err


def process_landing_page_revision(
    request_id: str,
    revision_id: str,
    *,
    mark_failed_on_error: bool = True,
) -> None:
    try:
        revision = get_landing_page_revision(revision_id)
        if revision is None or revision.get("request_id") != request_id:
            raise ValueError("처리할 Revision을 찾을 수 없습니다.")
        revision_status = revision.get("status")
        if revision_status == "QUEUED":
            started_at = _now()
            mark_landing_page_revision_processing(
                revision_id,
                processing_started_at=started_at,
                updated_at=started_at,
            )
            revision_status = "PROCESSING"
        if revision_status != "PROCESSING":
            return

        source_revision_id = revision.get("source_revision_id")
        source_revision = get_landing_page_revision(source_revision_id)
        if (
            source_revision is None
            or source_revision.get("request_id") != request_id
            or source_revision.get("status") != "COMPLETED"
        ):
            raise ValueError("완료된 기준 Revision을 찾을 수 없습니다.")

        source_bucket = source_revision.get("html_s3_bucket")
        source_key = source_revision.get("html_s3_key")
        if not source_bucket or not source_key:
            raise ValueError("기준 Revision에 HTML 저장 정보가 없습니다.")

        request_item = get_landing_page_request(request_id)
        if request_item is None:
            raise ValueError("Revision의 Request를 찾을 수 없습니다.")

        source_html = get_landing_page_html(bucket=source_bucket, key=source_key)
        generated, input_tokens, output_tokens = revise_landing_page(
            source_html=source_html,
            revision_prompt=revision["revision_prompt"],
        )
        storage = upload_landing_page_revision_html(
            user_id=request_item["user_id"],
            request_id=request_id,
            revision_id=revision_id,
            html=generated.html,
        )
        complete_landing_page_revision(
            request_id,
            revision_id,
            title=generated.title,
            html_s3_bucket=storage["html_s3_bucket"],
            html_s3_key=storage["html_s3_key"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            updated_at=_now(),
        )
    except Exception as err:
        if mark_failed_on_error:
            try:
                mark_landing_page_revision_failed(
                    revision_id,
                    error_type=type(err).__name__,
                    error_message=str(err),
                    updated_at=_now(),
                )
            except Exception as mark_err:
                print(
                    "[Revision] failed to mark revision as FAILED "
                    f"request_id={request_id} revision_id={revision_id} error={mark_err}"
                )
        print(
            "[Revision] processing failed "
            f"request_id={request_id} revision_id={revision_id} "
            f"error_type={type(err).__name__} error={err}"
        )
        raise


def get_revision(
    request_id: str,
    revision_id: str,
    current_user: dict,
) -> dict:
    try:
        _get_accessible_request(request_id, current_user)
        revision = get_landing_page_revision(revision_id)
        if revision is None or revision.get("request_id") != request_id:
            raise HTTPException(status_code=404, detail="Revision을 찾을 수 없습니다.")
        return revision
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as err:
        raise HTTPException(status_code=503, detail=f"Revision 조회에 실패했습니다. 원인: {err}") from err


def list_revisions(request_id: str, current_user: dict) -> dict:
    try:
        _get_accessible_request(request_id, current_user)
        items = list_landing_page_revisions(request_id)
        return {"items": items, "total": len(items)}
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as err:
        raise HTTPException(status_code=503, detail=f"Revision 목록 조회에 실패했습니다. 원인: {err}") from err
