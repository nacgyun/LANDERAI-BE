from pydantic import BaseModel


class TimestampResponse(BaseModel):
    created_at: str
    updated_at: str
    deleted_at: str | None = None