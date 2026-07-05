from decimal import Decimal

from app.config.settings import settings
from app.services.openai_service import generate_embedding


def build_request_embedding_input(request_item: dict) -> str:
    parts = [
        f"industry: {request_item['industry']}",
        f"sub_industry: {request_item['sub_industry']}",
        f"target: {request_item['target']}",
        f"style: {request_item['style']}",
        f"goal: {request_item['goal']}",
        f"language: {request_item['language']}",
    ]

    additional_context = request_item.get("additional_context")
    if additional_context:
        parts.append(f"additional_context: {additional_context}")

    return "\n".join(parts)


def create_request_embedding(request_item: dict) -> dict:
    embedding_input = build_request_embedding_input(request_item)
    embedding, input_tokens = generate_embedding(embedding_input)

    return {
        "embedding": [Decimal(str(value)) for value in embedding],
        "embedding_model": settings.OPENAI_EMBEDDING_MODEL,
        "embedding_input": embedding_input,
        "embedding_input_tokens": input_tokens,
    }
