from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    OPENAI_API_KEY: str | None = None
    OPENAI_API_KEY_PARAMETER_NAME: str | None = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_INPUT_TOKEN_PRICE_PER_TOKEN: Decimal = Decimal("0.00000015")
    OPENAI_OUTPUT_TOKEN_PRICE_PER_TOKEN: Decimal = Decimal("0.0000006")
    DYNAMODB_ENDPOINT_URL: str = "http://localhost:8000"
    REGION_NAME: str = "ap-northeast-2"
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_SESSION_TOKEN: str | None = None
    AWS_DEFAULT_REGION: str | None = None
    CORE_TABLE_NAME: str = "coreTable"
    LANDING_REQUEST_TABLE_NAME: str = "LandingRequest"
    LANDING_RESULT_TABLE_NAME: str = "LandingResult"
    RAG_DESIGN_PLAN_TABLE_NAME: str = "RAG_DESIGNPLAN"
    LANDING_PAGE_STATE_MACHINE_ARN: str | None = None
    S3_BUCKET_NAME: str | None = None
    S3_ENDPOINT_URL: str | None = None
    S3_PUBLIC_BASE_URL: str | None = None
    S3_VECTOR_BUCKET_NAME: str | None = "landerai-designplan-vector"
    S3_VECTOR_INDEX_NAME: str | None = None
    S3_VECTOR_ENDPOINT_URL: str | None = None
    S3_VECTOR_NAMESPACE: str = "landing-page-design-plans"
    LOCAL_STORAGE_DIR: str = ".local-storage"

    APP_ENV: str = "local"
    AUTH_MODE: str = "dev"

    CLERK_SECRET_KEY: str | None = None
    CLERK_SECRET_KEY_PARAMETER_NAME: str | None = None
    CLERK_PUBLISHABLE_KEY: str | None = None
    CLERK_AUTHORIZED_PARTY: str = "http://localhost:3000"

    DEV_USER_ID: str = "dev_user_001"
    DEV_USER_ROLE: str = "USER"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
