from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.common.auth import require_user_or_admin
from app.common.workflow_status import STATUS_PROCESSING, STEP_EMBEDDING
from app.config.settings import settings
from app.pipelines.landing_page.orchestrator import run_landing_page_pipeline
from app.repositories.request_repository import (
    mark_landing_page_request_failed,
    save_landing_page_workflow_execution,
)
from app.schemas.request import (
    LandingPageCreateRequest,
    LandingPagePreviewUrlsResponse,
    LandingPageRequestCreateResponse,
    LandingPageRequestStatusResponse,
    LandingPageVariantSelectionRequest,
    LandingPageVariantSelectionResponse,
)
from app.services.preview_service import get_landing_page_preview_urls
from app.services.request_service import (
    create_landing_page_request,
    get_landing_page_request_status,
    get_landing_page_variant_selection,
    select_landing_page_variant,
)
from app.services.workflow_service import start_landing_page_workflow


router = APIRouter(tags=["requests"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/api/v1/requests", response_model=LandingPageRequestCreateResponse)
def create_landing_page_request_endpoint(
    request: LandingPageCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_user_or_admin),
):
    response = create_landing_page_request(request, current_user)
    if settings.LANDING_PAGE_STATE_MACHINE_ARN:
        try:
            workflow = start_landing_page_workflow(response["request_id"])
            save_landing_page_workflow_execution(
                response["request_id"],
                execution_arn=workflow["execution_arn"],
                start_date=workflow["start_date"],
                status=STATUS_PROCESSING,
                progress=1,
                updated_at=_now(),
            )
            response["status"] = STATUS_PROCESSING
            response["progress"] = 1
            response["workflow_execution_arn"] = workflow["execution_arn"]
            response["workflow_start_date"] = workflow["start_date"]
            print(
                "[Workflow] started landing page workflow "
                f"request_id={response['request_id']} "
                f"execution_arn={workflow['execution_arn']}"
            )
        except (BotoCoreError, ClientError, ValueError) as workflow_err:
            error_type = type(workflow_err).__name__
            error_message = str(workflow_err)
            try:
                mark_landing_page_request_failed(
                    response["request_id"],
                    current_step=STEP_EMBEDDING,
                    error_type=error_type,
                    error_message=error_message,
                    updated_at=_now(),
                )
            except (BotoCoreError, ClientError):
                pass
            print(
                "[Workflow] failed to start landing page workflow "
                f"request_id={response['request_id']} "
                f"error_type={error_type} error={error_message}"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "랜딩페이지 워크플로우 시작에 실패했습니다.",
                    "request_id": response["request_id"],
                    "error_type": error_type,
                    "error_message": error_message,
                },
            ) from workflow_err
    else:
        background_tasks.add_task(run_landing_page_pipeline, response["request_id"])
    return response


@router.get(
    "/api/v1/requests/{request_id}",
    response_model=LandingPageRequestStatusResponse,
)
def get_landing_page_request_status_endpoint(
    request_id: str,
    current_user: dict = Depends(require_user_or_admin),
):
    return get_landing_page_request_status(request_id, current_user)


@router.get(
    "/api/v1/requests/{request_id}/preview-urls",
    response_model=LandingPagePreviewUrlsResponse,
)
def get_landing_page_preview_urls_endpoint(
    request_id: str,
    current_user: dict = Depends(require_user_or_admin),
):
    return get_landing_page_preview_urls(request_id, current_user)


@router.get(
    "/api/v1/requests/{request_id}/variant-selection",
    response_model=LandingPageVariantSelectionResponse,
)
def get_landing_page_variant_selection_endpoint(
    request_id: str,
    current_user: dict = Depends(require_user_or_admin),
):
    return get_landing_page_variant_selection(request_id, current_user)


@router.post(
    "/api/v1/requests/{request_id}/variant-selection",
    response_model=LandingPageVariantSelectionResponse,
)
def select_landing_page_variant_endpoint(
    request_id: str,
    request: LandingPageVariantSelectionRequest,
    current_user: dict = Depends(require_user_or_admin),
):
    return select_landing_page_variant(request_id, request, current_user)
