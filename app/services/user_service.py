from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
import httpx
from fastapi import HTTPException, status

from app.config.settings import settings
from app.config.secrets import get_clerk_secret_key
from app.repositories.user_repository import signup_user_profile, soft_delete_user_profile
from app.schemas.user import UserSignupRequest


def _split_name(name: str) -> tuple[str, str | None]:
    parts = name.strip().split(maxsplit=1)
    if not parts:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]

#clerk 회원가입 API
def _create_clerk_user(request: UserSignupRequest) -> dict:
    clerk_secret_key = get_clerk_secret_key()
    if not clerk_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLERK_SECRET_KEY is required to create Clerk users",
        )

    first_name, last_name = _split_name(request.name)
    payload = {
        "email_address": [str(request.email)],
        "password": request.password,
        "first_name": first_name,
        "public_metadata": {
            "role": "USER",
        },
    }
    if last_name:
        payload["last_name"] = last_name

    try:
        response = httpx.post(
            "https://api.clerk.com/v1/users",
            headers={
                "Authorization": f"Bearer {clerk_secret_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as clerk_err:
        try:
            detail = clerk_err.response.json()
        except ValueError:
            detail = clerk_err.response.text
        raise HTTPException(
            status_code=clerk_err.response.status_code,
            detail=detail,
        ) from clerk_err
    except httpx.HTTPError as clerk_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Clerk user creation failed: {clerk_err}",
        ) from clerk_err

    return response.json()


def _delete_clerk_user(user_id: str) -> None:
    clerk_secret_key = get_clerk_secret_key()
    if not clerk_secret_key:
        return

    try:
        httpx.delete(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {clerk_secret_key}"},
            timeout=10.0,
        )
    except httpx.HTTPError:
        pass


def signup(request: UserSignupRequest) -> dict:
    clerk_user = _create_clerk_user(request)
    user_id = clerk_user["id"]
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "PK": f"USER#{user_id}",
        "SK": "PROFILE",
        "item_type": "USER",
        "user_id": user_id,
        "email": str(request.email),
        "name": request.name,
        "last_active_project_id": None,
        "role": "USER",
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }

    try:
        signup_user_profile(item)
    except (BotoCoreError, ClientError) as db_err:
        _delete_clerk_user(user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"데이터베이스 연결에 실패했습니다. 로컬 컨테이너 상태를 확인하세요. 원인: {db_err}",
        ) from db_err

    return {
        "user_id": item["user_id"],
        "email": item["email"],
        "name": item["name"],
        "last_active_project_id": item["last_active_project_id"],
        "role": item["role"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "deleted_at": item["deleted_at"],
        "clerk_user": {
            "id": clerk_user.get("id"),
            "email_addresses": clerk_user.get("email_addresses", []),
        },
    }


def get_current_user_info(current_user: dict) -> dict:
    claims = current_user.get("claims", {})
    return {
        "current_user": {
            "user_id": current_user["user_id"],
            "role": current_user.get("role"),
            "email": claims.get("email")
            or claims.get("email_address")
            or claims.get("primary_email_address"),
            "claims": {
                "sub": claims.get("sub"),
                "iss": claims.get("iss"),
                "azp": claims.get("azp"),
                "sid": claims.get("sid"),
                "role": claims.get("role")
                or claims.get("org_role")
                or (claims.get("public_metadata") or {}).get("role"),
            },
        },
    }


def delete_user(current_user: dict) -> dict:
    user_id = current_user["user_id"]
    now = datetime.now(timezone.utc).isoformat()

    try:
        soft_delete_user_profile(user_id, now)
    except (BotoCoreError, ClientError) as db_err:
        if isinstance(db_err, ClientError):
            error_code = db_err.response.get("Error", {}).get("Code")
            if error_code == "ConditionalCheckFailedException":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User profile not found",
                ) from db_err

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"데이터베이스 연결에 실패했습니다. 로컬 컨테이너 상태를 확인하세요. 원인: {db_err}",
        ) from db_err

    if settings.AUTH_MODE == "clerk":
        _delete_clerk_user(user_id)

    return {
        "status": "deleted",
        "user_id": user_id,
        "deleted_at": now,
        "updated_at": now,
    }
