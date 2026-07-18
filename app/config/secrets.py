from functools import lru_cache

import boto3

from app.config.settings import settings


@lru_cache
def get_parameter_value(parameter_name: str) -> str:
    client = boto3.client(
        "ssm",
        region_name=settings.AWS_DEFAULT_REGION or settings.REGION_NAME,
    )
    response = client.get_parameter(
        Name=parameter_name,
        WithDecryption=True,
    )
    return response["Parameter"]["Value"]


def get_openai_api_key() -> str | None:
    if settings.OPENAI_API_KEY:
        return settings.OPENAI_API_KEY
    if settings.OPENAI_API_KEY_PARAMETER_NAME:
        return get_parameter_value(settings.OPENAI_API_KEY_PARAMETER_NAME)
    return None


def get_clerk_secret_key() -> str | None:
    if settings.CLERK_SECRET_KEY:
        return settings.CLERK_SECRET_KEY
    if settings.CLERK_SECRET_KEY_PARAMETER_NAME:
        return get_parameter_value(settings.CLERK_SECRET_KEY_PARAMETER_NAME)
    return None
