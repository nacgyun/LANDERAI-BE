from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
import httpx
from fastapi import HTTPException, status

from app.common.auth import _extract_role
from app.config.settings import settings
from app.config.secrets import get_clerk_secret_key
from app.repositories.user_repository import (
    get_user_profile,
    signup_user_profile,
    soft_delete_user_profile,
)
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


def _get_clerk_user_profile(user_id: str) -> dict:
    clerk_secret_key = get_clerk_secret_key()
    if not clerk_secret_key:
        return {}

    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {clerk_secret_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return {}

    clerk_user = response.json()
    first_name = clerk_user.get("first_name") or ""
    last_name = clerk_user.get("last_name") or ""
    name = " ".join(part for part in (first_name, last_name) if part).strip() or None

    primary_email_id = clerk_user.get("primary_email_address_id")
    email_addresses = clerk_user.get("email_addresses") or []
    primary_email = next(
        (
            item.get("email_address")
            for item in email_addresses
            if item.get("id") == primary_email_id
        ),
        None,
    )
    if primary_email is None and email_addresses:
        primary_email = email_addresses[0].get("email_address")

    created_at = clerk_user.get("created_at")
    if isinstance(created_at, int):
        created_at = datetime.fromtimestamp(
            created_at / 1000,
            tz=timezone.utc,
        ).isoformat()

    return {
        "name": name,
        "email": primary_email,
        "created_at": created_at,
    }


def initialize_current_user(current_user: dict) -> dict:
    """Initialize a Clerk-hosted signup as a LANDERAI USER exactly once."""
    clerk_secret_key = get_clerk_secret_key()
    if not clerk_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CLERK_SECRET_KEY is required to initialize Clerk users",
        )

    user_id = current_user["user_id"]
    headers = {
        "Authorization": f"Bearer {clerk_secret_key}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        clerk_user = response.json()

        role = _extract_role(clerk_user)
        if role is None:
            response = httpx.patch(
                f"https://api.clerk.com/v1/users/{user_id}/metadata",
                headers=headers,
                json={"public_metadata": {"role": "USER"}},
                timeout=10.0,
            )
            response.raise_for_status()
            role = "USER"
    except httpx.HTTPStatusError as clerk_err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Clerk 사용자 초기화가 거부되었습니다.",
                "clerk_status_code": clerk_err.response.status_code,
            },
        ) from clerk_err
    except httpx.HTTPError as clerk_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clerk 사용자 초기화 요청에 실패했습니다.",
        ) from clerk_err

    first_name = clerk_user.get("first_name") or ""
    last_name = clerk_user.get("last_name") or ""
    name = " ".join(part for part in (first_name, last_name) if part).strip()
    primary_email_id = clerk_user.get("primary_email_address_id")
    email_addresses = clerk_user.get("email_addresses") or []
    email = next(
        (
            item.get("email_address")
            for item in email_addresses
            if item.get("id") == primary_email_id
        ),
        None,
    )
    if email is None and email_addresses:
        email = email_addresses[0].get("email_address")

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "PK": f"USER#{user_id}",
        "SK": "PROFILE",
        "item_type": "USER",
        "user_id": user_id,
        "email": email,
        "name": name or email or user_id,
        "last_active_project_id": None,
        "role": role,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }

    initialized = False
    try:
        signup_user_profile(item)
        initialized = True
    except ClientError as db_err:
        error_code = db_err.response.get("Error", {}).get("Code")
        if error_code != "ConditionalCheckFailedException":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="사용자 프로필 초기화에 실패했습니다.",
            ) from db_err
    except BotoCoreError as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="사용자 프로필 초기화에 실패했습니다.",
        ) from db_err

    return {
        "initialized": initialized,
        "user_id": user_id,
        "role": role,
    }


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
    role = current_user.get("role") or _extract_role(claims)
    profile = get_user_profile(current_user["user_id"])

    if profile and profile.get("deleted_at") is None:
        return {
            "user_id": profile["user_id"],
            "email": profile.get("email"),
            "name": profile.get("name"),
            "role": profile.get("role") or role,
            "last_active_project_id": profile.get("last_active_project_id"),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
            "profile_source": "landerai",
        }

    clerk_profile = (
        _get_clerk_user_profile(current_user["user_id"])
        if settings.AUTH_MODE == "clerk"
        else {}
    )
    return {
        "user_id": current_user["user_id"],
        "email": clerk_profile.get("email")
        or claims.get("email")
        or claims.get("email_address")
        or claims.get("primary_email_address"),
        "name": clerk_profile.get("name")
        or claims.get("name")
        or claims.get("first_name"),
        "role": role,
        "last_active_project_id": None,
        "created_at": clerk_profile.get("created_at"),
        "updated_at": None,
        "profile_source": "clerk_fallback",
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
