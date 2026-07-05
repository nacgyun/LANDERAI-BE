from fastapi import APIRouter

from app.schemas.design_plan import DesignPlanCreateRequest
from app.services.design_plan_service import create_design_plan


router = APIRouter(prefix="/api/v1", tags=["design-plan"])


@router.post("/designPlan")
def create_design_plan_endpoint(request: DesignPlanCreateRequest):
    return create_design_plan(request)
