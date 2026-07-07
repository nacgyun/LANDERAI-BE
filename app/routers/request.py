from fastapi import APIRouter, BackgroundTasks, Depends

from app.common.auth import require_user_or_admin
from app.config.settings import settings
from app.pipelines.landing_page.orchestrator import run_landing_page_pipeline
from app.schemas.request import (
    LandingPageCreateRequest,
    LandingPagePreviewUrlsResponse,
    LandingPageRequestCreateResponse,
    LandingPageVariantSelectionRequest,
    LandingPageVariantSelectionResponse,
)
from app.services.preview_service import get_landing_page_preview_urls
from app.services.request_service import (
    create_landing_page_request,
    get_landing_page_variant_selection,
    select_landing_page_variant,
)
from app.services.workflow_service import start_landing_page_workflow


router = APIRouter(tags=["requests"])


@router.post("/api/v1/requests", response_model=LandingPageRequestCreateResponse)
def create_landing_page_request_endpoint(
    request: LandingPageCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_user_or_admin),
):
    response = create_landing_page_request(request, current_user)
    if settings.LANDING_PAGE_STATE_MACHINE_ARN:
        start_landing_page_workflow(response["request_id"])
    else:
        background_tasks.add_task(run_landing_page_pipeline, response["request_id"])
    return response


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
