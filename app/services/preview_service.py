from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.repositories.request_repository import (
    get_landing_page_request,
    get_landing_page_result,
)
from app.repositories.s3_repository import create_landing_page_preview_url


PREVIEW_URL_EXPIRES_IN_SECONDS = 900


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


def _can_access_request(request_item: dict, current_user: dict) -> bool:
    if current_user.get("role") == "ADMIN":
        return True
    return request_item.get("user_id") == current_user["user_id"]


def get_landing_page_preview_urls(
    request_id: str,
    current_user: dict,
) -> dict:
    try:
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

        request_status = request_item.get("status")
        if request_status != "COMPLETED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "랜딩페이지 생성이 아직 완료되지 않았습니다.",
                    "status": request_status,
                    "current_step": request_item.get("current_step"),
                    "progress": _to_json_safe_value(request_item.get("progress")),
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

        previews = {}
        for variant_name, variant_payload in result_item.get("variants", {}).items():
            bucket = variant_payload.get("html_s3_bucket")
            key = variant_payload.get("html_s3_key")
            if not bucket or not key:
                continue

            previews[variant_name] = {
                "preview_url": create_landing_page_preview_url(
                    bucket=bucket,
                    key=key,
                    expires_in=PREVIEW_URL_EXPIRES_IN_SECONDS,
                ),
                "html_s3_bucket": bucket,
                "html_s3_key": key,
            }

        if not previews:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="프리뷰 URL을 만들 수 있는 HTML 저장 정보가 없습니다.",
            )

        return {
            "request_id": request_id,
            "status": "COMPLETED",
            "expires_in": PREVIEW_URL_EXPIRES_IN_SECONDS,
            "variants": previews,
        }
    except HTTPException:
        raise
    except (BotoCoreError, ClientError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"프리뷰 URL 생성 중 AWS 또는 DB 요청에 실패했습니다. 원인: {err}",
        ) from err
