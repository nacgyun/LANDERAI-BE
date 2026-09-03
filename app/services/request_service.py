import copy
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import openai
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.common.workflow_status import (
    STATUS_COMPLETED,
    STATUS_QUEUED,
    STEP_EMBEDDING,
)
from app.common.mutation_paths import set_mutation_path
from app.repositories.request_repository import (
    get_landing_page_request,
    get_landing_page_result,
    save_landing_page_request,
    save_initial_revision_and_landing_page_variant_selection,
)
from app.repositories.rag_design_plan_repository import (
    save_rag_design_plan,
    update_rag_design_plan_vector_reference,
)
from app.repositories.s3_vector_repository import save_design_plan_vector
from app.schemas.request import (
    LandingPageCreateRequest,
    LandingPageVariantSelectionRequest,
)
from app.schemas.revision import LandingPageRevision
from app.config.settings import settings
from app.services.openai_service import generate_embedding


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _can_access_request(request_item: dict, current_user: dict) -> bool:
    if current_user.get("role") == "ADMIN":
        return True
    return request_item.get("user_id") == current_user["user_id"]


def _get_accessible_landing_page_request(
    request_id: str,
    current_user: dict,
) -> dict:
    request_item = get_landing_page_request(request_id)
    if request_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="요청을 찾을 수 없습니다.",
        )

    if not _can_access_request(request_item, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 요청에 접근할 권한이 없습니다.",
        )

    return request_item


def _json_default(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _to_json_safe_value(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {
            key: _to_json_safe_value(child_value)
            for key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_to_json_safe_value(child_value) for child_value in value]
    return value


def _build_design_plan_vector_input(
    *,
    rag_design_plan_item: dict,
) -> str:
    return json.dumps(rag_design_plan_item, ensure_ascii=False, default=_json_default)


def _build_rag_design_plan_id(*, request_id: str, variant: str) -> str:
    return f"dp_{request_id}_{variant}"


def _apply_mutation_to_design_plan(
    design_plan: dict,
    mutation: dict,
) -> dict:
    mutated_plan = copy.deepcopy(design_plan)
    for patch in mutation.get("patch", []):
        set_mutation_path(mutated_plan, patch["path"], patch["value"])
    return mutated_plan


def _resolve_selected_design_plan(
    *,
    request_item: dict,
    selected_variant: str,
    variant_payload: dict,
) -> dict:
    design_plan_json = request_item.get("design_plan_json")
    if design_plan_json:
        design_plan_payload = json.loads(design_plan_json)
        base_plan = design_plan_payload.get("design_plan")
        mutation = design_plan_payload.get("mutation")

        if selected_variant == "A" and isinstance(base_plan, dict):
            return base_plan

        if (
            selected_variant == "B"
            and isinstance(base_plan, dict)
            and isinstance(mutation, dict)
        ):
            return _apply_mutation_to_design_plan(base_plan, mutation)

    selected_plan = variant_payload.get("plan")
    if isinstance(selected_plan, dict):
        return selected_plan

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="선택한 variant에 저장할 design plan이 없습니다.",
    )


def create_landing_page_request(
    request: LandingPageCreateRequest,
    current_user: dict,
) -> dict:
    now = _now()
    item = {
        "request_id": f"req_{uuid.uuid4().hex}",
        "user_id": current_user["user_id"],
        "industry": request.industry,
        "sub_industry": request.sub_industry,
        "target": request.target,
        "style": request.style,
        "goal": request.goal,
        "additional_context": request.additional_context,
        "language": request.language,
        "status": STATUS_QUEUED,
        "current_step": STEP_EMBEDDING,
        "progress": 0,
        "project_id": None,
        "error_message": None,
        "selection_status": "NOT_SELECTED",
        "chosen_variant": None,
        "latest_revision_id": None,
        "published_revision_id": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        save_landing_page_request(item)
    except (BotoCoreError, ClientError) as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"데이터베이스 연결에 실패했습니다. 로컬 컨테이너 상태를 확인하세요. 원인: {db_err}",
        ) from db_err

    return {
        "request_id": item["request_id"],
        "status": item["status"],
        "current_step": item["current_step"],
        "progress": item["progress"],
        "project_id": item["project_id"],
        "created_at": item["created_at"],
        "workflow_execution_arn": item.get("workflow_execution_arn"),
        "workflow_start_date": item.get("workflow_start_date"),
    }


def get_landing_page_request_status(
    request_id: str,
    current_user: dict,
) -> dict:
    try:
        request_item = _get_accessible_landing_page_request(request_id, current_user)
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"요청 상태 조회 중 DB 요청에 실패했습니다. 원인: {db_err}",
        ) from db_err

    return {
        "request_id": request_item["request_id"],
        "status": request_item.get("status"),
        "current_step": request_item.get("current_step"),
        "progress": _to_json_safe_value(request_item.get("progress")),
        "project_id": request_item.get("project_id"),
        "error_type": request_item.get("error_type"),
        "error_message": request_item.get("error_message"),
        "workflow_execution_arn": request_item.get("workflow_execution_arn"),
        "workflow_start_date": request_item.get("workflow_start_date"),
        "landing_result_id": request_item.get("landing_result_id"),
        "selection_status": request_item.get("selection_status"),
        "chosen_variant": request_item.get("chosen_variant"),
        "latest_revision_id": request_item.get("latest_revision_id"),
        "published_revision_id": request_item.get("published_revision_id"),
        "created_at": request_item.get("created_at"),
        "updated_at": request_item.get("updated_at"),
    }


def get_landing_page_variant_selection(
    request_id: str,
    current_user: dict,
) -> dict:
    try:
        request_item = _get_accessible_landing_page_request(request_id, current_user)
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"요청 선택 상태 조회 중 DB 요청에 실패했습니다. 원인: {db_err}",
        ) from db_err

    chosen_variant = request_item.get("chosen_variant")
    if request_item.get("selection_status") != "SELECTED" or not chosen_variant:
        return {
            "request_id": request_id,
            "status": STATUS_COMPLETED,
            "selection_status": "NOT_SELECTED",
            "chosen_variant": None,
            "selected_design_plan_id": None,
            "design_plan_vector_key": None,
            "latest_revision_id": None,
            "published_revision_id": None,
        }

    return {
        "request_id": request_id,
        "status": STATUS_COMPLETED,
        "selection_status": "SELECTED",
        "chosen_variant": chosen_variant,
        "selected_design_plan_id": request_item.get("selected_design_plan_id"),
        "design_plan_vector_key": request_item.get("design_plan_vector_key"),
        "latest_revision_id": request_item.get("latest_revision_id"),
        "published_revision_id": request_item.get("published_revision_id"),
    }


def select_landing_page_variant(
    request_id: str,
    request: LandingPageVariantSelectionRequest,
    current_user: dict,
) -> dict:
    try:
        request_item = _get_accessible_landing_page_request(request_id, current_user)

        if (
            request_item.get("selection_status") == "SELECTED"
            or request_item.get("chosen_variant")
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A/B안 선택이 이미 완료되었습니다.",
            )

        request_status = request_item.get("status")
        if request_status != STATUS_COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "선택 가능한 A/B안이 아직 생성되지 않았습니다.",
                    "status": request_status,
                    "current_step": request_item.get("current_step"),
                    "progress": _to_json_safe_value(request_item.get("progress")),
                    "selection_status": "NOT_SELECTED",
                },
            )

        landing_result_id = request_item.get("landing_result_id")
        if not landing_result_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="완료된 요청에 결과 참조가 없습니다.",
            )

        result_item = get_landing_page_result(landing_result_id)
        if result_item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="랜딩페이지 결과를 찾을 수 없습니다.",
            )

        variants = result_item.get("variants", {})
        if request.selected_variant not in variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "선택한 variant가 생성 결과에 없습니다.",
                    "available_variants": sorted(variants.keys()),
                },
            )

        variant_payload = variants[request.selected_variant]
        selected_design_plan = _resolve_selected_design_plan(
            request_item=request_item,
            selected_variant=request.selected_variant,
            variant_payload=variant_payload,
        )

        now = _now()
        selected_design_plan_id = _build_rag_design_plan_id(
            request_id=request_id,
            variant=request.selected_variant,
        )
        rag_design_plan_item = {
            "design_plan_id": selected_design_plan_id,
            "request_id": request_id,
            "result_id": landing_result_id,
            "project_id": request_item.get("project_id"),
            "user_id": request_item["user_id"],
            "industry": request_item.get("industry"),
            "sub_industry": request_item.get("sub_industry"),
            "target": request_item.get("target"),
            "style": request_item.get("style"),
            "goal": request_item.get("goal"),
            "additional_context": request_item.get("additional_context"),
            "language": request_item.get("language"),
            "chosen_variant": request.selected_variant,
            "variant_title": variant_payload.get("title"),
            "design_plan": selected_design_plan,
            "created_at": now,
            "updated_at": now,
        }
        save_rag_design_plan(rag_design_plan_item)

        vector_input = _build_design_plan_vector_input(
            rag_design_plan_item=rag_design_plan_item,
        )
        vector_embedding, vector_input_tokens = generate_embedding(vector_input)
        vector_storage = save_design_plan_vector(
            request_id=request_id,
            variant=request.selected_variant,
            industry=request_item.get("industry"),
            embedding=vector_embedding,
            metadata={
                "request_id": request_id,
                "design_plan_id": selected_design_plan_id,
                "project_id": request_item.get("project_id"),
                "industry": request_item.get("industry"),
                "sub_industry": request_item.get("sub_industry"),
                "target": request_item.get("target"),
                "goal": request_item.get("goal"),
                "language": request_item.get("language"),
                "chosen_variant": request.selected_variant,
                "created_at": now,
            },
        )

        update_rag_design_plan_vector_reference(
            selected_design_plan_id,
            vector_store=vector_storage["design_plan_vector_store"],
            vector_bucket=vector_storage["design_plan_vector_bucket"],
            vector_index=vector_storage["design_plan_vector_index"],
            vector_key=vector_storage["design_plan_vector_key"],
            vector_uri=vector_storage["design_plan_vector_uri"],
            embedding_model=settings.OPENAI_EMBEDDING_MODEL,
            embedding_input=vector_input,
            embedding_input_tokens=vector_input_tokens,
            updated_at=now,
        )

        revision_id = f"rev_{uuid.uuid4().hex}"
        initial_revision = LandingPageRevision(
            revision_id=revision_id,
            request_id=request_id,
            source_revision_id=None,
            revision_prompt=None,
            source_type="VARIANT",
            source_variant=request.selected_variant,
            html_s3_bucket=variant_payload.get("html_s3_bucket"),
            html_s3_key=variant_payload.get("html_s3_key"),
            created_at=now,
            updated_at=now,
            status="COMPLETED",
        )
        initial_revision_item = {
            # LandingResult uses result_id as its table partition key.
            "result_id": revision_id,
            "item_type": "REVISION",
            **initial_revision.model_dump(),
        }
        if (
            not initial_revision_item["html_s3_bucket"]
            or not initial_revision_item["html_s3_key"]
        ):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="선택한 variant의 HTML 저장 정보가 없습니다.",
            )

        save_initial_revision_and_landing_page_variant_selection(
            request_id,
            revision_item=initial_revision_item,
            chosen_variant=request.selected_variant,
            selected_design_plan_id=selected_design_plan_id,
            design_plan_vector_store=vector_storage["design_plan_vector_store"],
            design_plan_vector_bucket=vector_storage["design_plan_vector_bucket"],
            design_plan_vector_index=vector_storage["design_plan_vector_index"],
            design_plan_vector_key=vector_storage["design_plan_vector_key"],
            design_plan_vector_uri=vector_storage["design_plan_vector_uri"],
            selected_at=now,
            updated_at=now,
        )

        return {
            "request_id": request_id,
            "status": STATUS_COMPLETED,
            "selection_status": "SELECTED",
            "chosen_variant": request.selected_variant,
            "selected_design_plan_id": selected_design_plan_id,
            "design_plan_vector_key": vector_storage["design_plan_vector_key"],
            "latest_revision_id": revision_id,
            "published_revision_id": None,
        }
    except HTTPException:
        raise
    except openai.OpenAIError as ai_err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"design plan embedding 생성 중 AI 요청에 실패했습니다. 원인: {ai_err}",
        ) from ai_err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"design plan vector 저장 설정 또는 데이터가 올바르지 않습니다. 원인: {err}",
        ) from err
    except (BotoCoreError, ClientError) as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"variant 선택 저장 중 DB 요청에 실패했습니다. 원인: {db_err}",
        ) from db_err
