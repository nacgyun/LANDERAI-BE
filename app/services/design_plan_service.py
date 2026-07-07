import uuid
from datetime import datetime

import openai
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.common.cost import calculate_chat_completion_cost
from app.common.workflow_status import STATUS_QUEUED
from app.repositories.dynamodb_setup import get_request_table, get_result_table
from app.schemas.design_plan import DesignPlanCreateRequest
from app.services.openai_service import generate_design_plan_json


def create_design_plan(request: DesignPlanCreateRequest) -> dict[str, str]:
    request_id = str(uuid.uuid4())
    request_table = get_request_table()
    result_table = get_result_table()
    
    try:
        request_table.put_item(Item={
            "request_id": request_id,
            "industry": request.industry,
            "target": request.target,
            "style": request.style,
            "purpose": request.purpose,
            "extra": request.extra or "",
            "status": STATUS_QUEUED,
            "created_at": datetime.utcnow().isoformat(),
        })
    except (BotoCoreError, ClientError) as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"데이터베이스 연결에 실패했습니다. 로컬 컨테이너 상태를 확인하세요. 원인: {db_err}",
        ) from db_err

    try:
        generated_design_plan, input_tokens, output_tokens = generate_design_plan_json(request)
    except ValidationError as validation_err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 응답이 요구 스키마를 만족하지 않습니다: {validation_err}",
        ) from validation_err
    except openai.AuthenticationError as auth_err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OpenAI API Key가 올바르지 않거나 만료되었습니다.",
        ) from auth_err
    except openai.RateLimitError as rate_limit_err:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="OpenAI API 할당량을 초과했거나 요청 제한에 걸렸습니다.",
        ) from rate_limit_err
    except openai.APITimeoutError as timeout_err:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="OpenAI API 응답 시간이 초과되었습니다. 다시 시도해 주세요.",
        ) from timeout_err
    except (openai.OpenAIError, ValueError) as ai_err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 엔진 내부 에러 발생: {ai_err}",
        ) from ai_err

    try:
        estimated_cost = calculate_chat_completion_cost(input_tokens, output_tokens)

        result_table.put_item(Item={
            "result_id": str(uuid.uuid4()),
            "request_id": request_id,
            "design_plan_json": generated_design_plan,
            "estimated_cost": estimated_cost,
            "created_at": datetime.utcnow().isoformat(),
        })
    except (BotoCoreError, ClientError) as db_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"랜딩페이지 코드는 정상 생성되었으나 DB 적재에 실패했습니다: {db_err}",
        ) from db_err

    return {"status": "success", "request_id": request_id}
