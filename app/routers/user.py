from fastapi import APIRouter, Depends

from app.common.auth import require_user_or_admin
from app.schemas.user import CurrentUserResponse, UserSignupRequest
from app.services.user_service import delete_user, get_current_user_info, signup


router = APIRouter(prefix="/api/v1", tags=["users"])

@router.post("/users")
def signup_endpoint(request: UserSignupRequest):
    return signup(request)


@router.get("/users", response_model=CurrentUserResponse)
def get_user_endpoint(current_user: dict = Depends(require_user_or_admin)):
    return get_current_user_info(current_user)


@router.delete("/users")
def delete_user_endpoint(current_user: dict = Depends(require_user_or_admin)):
    return delete_user(current_user)
