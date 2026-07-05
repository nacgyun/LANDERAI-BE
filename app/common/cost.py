from decimal import Decimal

from app.config.settings import settings


def calculate_chat_completion_cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * settings.OPENAI_INPUT_TOKEN_PRICE_PER_TOKEN
        + Decimal(output_tokens) * settings.OPENAI_OUTPUT_TOKEN_PRICE_PER_TOKEN
    )
