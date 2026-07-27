from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI

from app.repositories.dynamodb_setup import init_tables
from app.routers.auth import router as auth_router
from app.routers.request import router as request_router
from app.routers.user import router as user_router

app = FastAPI(
    title="LanderAI Ops Platform",
    description="Production-inspired Serverless LLMOps Architecture",
    version="1.0.0",
)

app.include_router(auth_router)
app.include_router(request_router)
app.include_router(user_router)

@app.on_event("startup")
def on_startup():
    try:
        init_tables()
    except (BotoCoreError, ClientError) as db_err:
        print(f"[CRITICAL] DynamoDB Local 연결 실패: {db_err}")


@app.get("/")
def read_root():
    return {"message": "LanderAI API Server is Running!"}
