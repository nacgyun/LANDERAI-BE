from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

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


@router.get("/dev/clerk-login", include_in_schema=False)
def get_clerk_login_page():
    static_path = Path(__file__).resolve().parents[1] / "static" / "clerk_login.html"
    return FileResponse(static_path)
