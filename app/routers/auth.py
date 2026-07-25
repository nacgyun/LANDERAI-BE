from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.common.auth import get_current_user
from app.config.settings import settings


router = APIRouter(tags=["auth"])


@router.get("/api/v1/auth/config")
def get_auth_config():
    return {
        "auth_mode": settings.AUTH_MODE,
        "clerk_publishable_key": settings.CLERK_PUBLISHABLE_KEY,
        "clerk_authorized_parties": [
            party.strip()
            for party in settings.CLERK_AUTHORIZED_PARTY.split(",")
            if party.strip()
        ],
    }


@router.get("/api/v1/auth/me")
def get_auth_me(current_user: dict = Depends(get_current_user)):
    token_claims = current_user.get("token_claims", {})
    claims = current_user.get("claims", {})
    role_keys = [
        "role",
        "org_role",
        "public_metadata",
        "publicMetadata",
        "metadata",
    ]
    return {
        "user_id": current_user["user_id"],
        "role": current_user.get("role"),
        "role_source": current_user.get("role_source"),
        "token_claim_keys": sorted(token_claims.keys()),
        "token_role_claims": {
            key: token_claims.get(key)
            for key in role_keys
            if key in token_claims
        },
        "resolved_role_claims": {
            key: claims.get(key)
            for key in role_keys
            if key in claims
        },
        "authorized_parties": [
            party.strip()
            for party in settings.CLERK_AUTHORIZED_PARTY.split(",")
            if party.strip()
        ],
    }


@router.get("/dev/clerk-login", include_in_schema=False)
def get_clerk_login_page():
    static_path = Path(__file__).resolve().parents[1] / "static" / "clerk_login.html"
    return FileResponse(static_path)
