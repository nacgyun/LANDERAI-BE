from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.repositories.dynamodb_setup import init_tables
from app.routers.auth import router as auth_router
from app.routers.request import router as request_router
from app.routers.user import router as user_router

app = FastAPI(
    title="LanderAI Ops Platform",
    description="Production-inspired Serverless LLMOps Architecture",
    version="1.0.0",
)

# API Gateway의 $default route가 OPTIONS 요청을 Lambda로 전달하는 경우에도
# 브라우저 preflight가 200으로 끝나도록 애플리케이션 계층에서 처리합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
