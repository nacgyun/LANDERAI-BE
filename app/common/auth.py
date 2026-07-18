from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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

    for metadata_key in ("public_metadata", "publicMetadata", "metadata"):
        metadata = claims.get(metadata_key)
        if isinstance(metadata, dict) and metadata.get("role"):
            return str(metadata["role"]).upper()

    return None

#사용자가 누구인지 확인 -> user_id, mode, role 반환
def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if settings.AUTH_MODE == "dev":
        if settings.APP_ENV == "production":
            raise RuntimeError("AUTH_MODE=dev is not allowed in production")

        return {
            "user_id": settings.DEV_USER_ID,
            "claims": {
                "sub": settings.DEV_USER_ID,
                "mode": "dev",
                "role": settings.DEV_USER_ROLE.upper(),
            },
        }

    if settings.AUTH_MODE != "clerk":
        raise RuntimeError(f"Unsupported AUTH_MODE: {settings.AUTH_MODE}")

    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    clerk_secret_key = get_clerk_secret_key()
    if not clerk_secret_key:
        raise RuntimeError("CLERK_SECRET_KEY is required when AUTH_MODE=clerk")

    sdk = Clerk(bearer_auth=clerk_secret_key)

    state = sdk.authenticate_request(
        request,
        AuthenticateRequestOptions(
            authorized_parties=_get_authorized_parties(),
            accepts_token=["session_token"],
        ),
    )

    if not state.is_signed_in:
        raise HTTPException(status_code=401, detail=state.reason)

    return {
        "user_id": state.payload["sub"],
        "claims": state.payload,
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
