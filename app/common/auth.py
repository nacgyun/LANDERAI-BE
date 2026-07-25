from collections.abc import Callable

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions

from app.config.settings import settings
from app.config.secrets import get_clerk_secret_key

bearer_scheme = HTTPBearer(auto_error=False)


def _get_authorized_parties() -> list[str]:
    return [
        party.strip()
        for party in settings.CLERK_AUTHORIZED_PARTY.split(",")
        if party.strip()
    ]

#role 대문자로 받환
def _extract_role(claims: dict) -> str | None:
    role = claims.get("role") or claims.get("org_role")
    if role:
        return str(role).upper()

    return _metadata_role(claims)


def _metadata_role(payload: dict) -> str | None:
    for metadata_key in ("public_metadata", "publicMetadata", "metadata"):
        metadata = payload.get(metadata_key)
        if isinstance(metadata, dict) and metadata.get("role"):
            return str(metadata["role"]).upper()
    return None


def _claims_to_dict(claims) -> dict:
    if isinstance(claims, dict):
        return claims
    if hasattr(claims, "model_dump"):
        return claims.model_dump()
    if hasattr(claims, "dict"):
        return claims.dict()
    if hasattr(claims, "__dict__"):
        return dict(claims.__dict__)
    raise TypeError(f"Unsupported Clerk claims payload type: {type(claims).__name__}")


def _fetch_clerk_user_role(user_id: str, clerk_secret_key: str) -> str | None:
    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={
                "Authorization": f"Bearer {clerk_secret_key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as clerk_err:
        print(
            "[Auth] failed to fetch Clerk user metadata "
            f"user_id={user_id} status_code={clerk_err.response.status_code} "
            f"error={clerk_err.response.text}"
        )
        return None
    except httpx.HTTPError as clerk_err:
        print(
            "[Auth] failed to fetch Clerk user metadata "
            f"user_id={user_id} error_type={type(clerk_err).__name__} "
            f"error={clerk_err}"
        )
        return None

    return _metadata_role(response.json())

#사용자가 누구인지 확인 -> user_id, mode, role 반환
def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if settings.AUTH_MODE == "dev":
        if settings.APP_ENV == "production":
            raise HTTPException(
                status_code=500,
                detail="AUTH_MODE=dev is not allowed in production",
            )

        return {
            "user_id": settings.DEV_USER_ID,
            "claims": {
                "sub": settings.DEV_USER_ID,
                "mode": "dev",
                "role": settings.DEV_USER_ROLE.upper(),
            },
        }

    if settings.AUTH_MODE != "clerk":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported AUTH_MODE: {settings.AUTH_MODE}",
        )

    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        clerk_secret_key = get_clerk_secret_key()
    except (BotoCoreError, ClientError) as secret_err:
        print(
            "[Auth] failed to load Clerk secret "
            f"parameter={settings.CLERK_SECRET_KEY_PARAMETER_NAME} "
            f"error_type={type(secret_err).__name__} error={secret_err}"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Clerk secret을 불러오지 못했습니다.",
                "error_type": type(secret_err).__name__,
                "parameter_name": settings.CLERK_SECRET_KEY_PARAMETER_NAME,
            },
        ) from secret_err
    except Exception as secret_err:
        print(
            "[Auth] unexpected error while loading Clerk secret "
            f"parameter={settings.CLERK_SECRET_KEY_PARAMETER_NAME} "
            f"error_type={type(secret_err).__name__} error={secret_err}"
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Clerk secret 설정 처리 중 예상치 못한 오류가 발생했습니다.",
                "error_type": type(secret_err).__name__,
                "parameter_name": settings.CLERK_SECRET_KEY_PARAMETER_NAME,
            },
        ) from secret_err

    if not clerk_secret_key:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "CLERK_SECRET_KEY 또는 CLERK_SECRET_KEY_PARAMETER_NAME 설정이 필요합니다.",
                "auth_mode": settings.AUTH_MODE,
            },
        )

    sdk = Clerk(bearer_auth=clerk_secret_key)

    try:
        state = sdk.authenticate_request(
            request,
            AuthenticateRequestOptions(
                authorized_parties=_get_authorized_parties(),
                accepts_token=["session_token"],
            ),
        )
    except Exception as clerk_err:
        print(
            "[Auth] Clerk request authentication failed "
            f"error_type={type(clerk_err).__name__} error={clerk_err}"
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Clerk 토큰 검증 중 오류가 발생했습니다.",
                "error_type": type(clerk_err).__name__,
                "authorized_parties": _get_authorized_parties(),
            },
        ) from clerk_err

    if not state.is_signed_in:
        raise HTTPException(status_code=401, detail=str(state.reason))

    try:
        claims = _claims_to_dict(state.payload)
        user_id = claims["sub"]
    except Exception as claims_err:
        print(
            "[Auth] failed to parse Clerk claims "
            f"error_type={type(claims_err).__name__} error={claims_err}"
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Clerk 인증 payload를 해석하지 못했습니다.",
                "error_type": type(claims_err).__name__,
            },
        ) from claims_err

    role = _extract_role(claims)
    if role is None:
        role = _fetch_clerk_user_role(user_id, clerk_secret_key)
        if role:
            claims["role"] = role
            print(
                "[Auth] resolved role from Clerk user metadata "
                f"user_id={user_id} role={role}"
            )

    return {
        "user_id": user_id,
        "claims": claims,
    }

#사용자  role 확인해서 권한 체크
def require_roles(*allowed_roles: str) -> Callable:
    allowed_role_set = {role.upper() for role in allowed_roles}

    def dependency(current_user: dict = Depends(get_current_user)) -> dict:
        role = _extract_role(current_user["claims"])
        if role not in allowed_role_set:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Insufficient role",
                    "required_roles": sorted(allowed_role_set),
                    "current_role": role,
                },
            )

        return {
            **current_user,
            "role": role,
        }

    return dependency


require_user_or_admin = require_roles("USER", "ADMIN")
require_admin = require_roles("ADMIN")
