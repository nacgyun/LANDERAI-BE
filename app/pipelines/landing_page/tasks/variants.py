import copy
import json
from typing import Any

from app.common.cost import calculate_chat_completion_cost
from app.common.mutation_paths import set_mutation_path
from app.services.openai_service import generate_landing_page_variant


def apply_mutation_to_design_plan(
    design_plan: dict[str, Any],
    mutation: dict[str, Any],
) -> dict[str, Any]:
    mutated_plan = copy.deepcopy(design_plan)
    for patch in mutation.get("patch", []):
        set_mutation_path(mutated_plan, patch["path"], patch["value"])
    return mutated_plan


def create_landing_page_variants(design_plan_json: str) -> dict:
    design_plan_payload = json.loads(design_plan_json)
    base_plan = design_plan_payload["design_plan"]
    mutation = design_plan_payload["mutation"]
    mutated_plan = apply_mutation_to_design_plan(base_plan, mutation)

    variant_a, a_input_tokens, a_output_tokens = generate_landing_page_variant(
        variant="A",
        design_plan=base_plan,
        mutation=None,
    )
    variant_b, b_input_tokens, b_output_tokens = generate_landing_page_variant(
        variant="B",
        design_plan=mutated_plan,
        mutation=mutation,
    )

    total_input_tokens = a_input_tokens + b_input_tokens
    total_output_tokens = a_output_tokens + b_output_tokens
    estimated_cost = calculate_chat_completion_cost(
        total_input_tokens,
        total_output_tokens,
    )

    return {
        "variants": {
            "A": {
                "variant": "A",
                "title": variant_a.title,
                "html": variant_a.html,
                "plan": base_plan,
            },
            "B": {
                "variant": "B",
                "title": variant_b.title,
                "html": variant_b.html,
                "plan": mutated_plan,
                "mutation": mutation,
            },
        },
        "landing_page_input_tokens": total_input_tokens,
        "landing_page_output_tokens": total_output_tokens,
        "landing_page_estimated_cost": estimated_cost,
    }
