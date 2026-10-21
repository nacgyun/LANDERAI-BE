"""One classified PATCH retry followed by FULL fallback when appropriate."""

POLICY_VERSION = "patch-retry-v1"
FULL_THRESHOLD = 30

GUIDANCE = {
    "selector": "Re-identify the intended target from the original HTML. Narrow or correct the selector. Do not merely change expected_count to accept unrelated elements.",
    "schema": "Return valid JSON matching the schema, with required fields and valid types. Preserve the original user intent.",
    "text": "Use an exact substring occurring once in a direct text node of the intended element. Inspect nested tags; do not flatten markup or choose an arbitrary duplicate.",
    "css": "Use supported CSS properties and valid values with the correct mode fields. Preserve the requested effect; do not silently omit unsupported requirements.",
    "insert": "Fix the insertion position, parent/child structure, depth and unique IDs. Use supported elements without dropping requested content.",
}


def retry_feedback(code: str, message: str) -> dict[str, str] | None:
    group = None
    if code in ("target_count_mismatch", "invalid_selector"):
        group = "selector"
    elif code == "ambiguous_text":
        group = "text"
    elif code in ("unsupported_css_property", "invalid_css_value"):
        group = "css"
    elif code in (
        "invalid_html_structure", "invalid_insert_parent", "invalid_insert_position",
        "insert_depth_exceeded", "duplicate_id", "duplicate_or_invalid_id",
        "insert_verification_failed",
    ):
        group = "insert"
    elif code == "invalid_patch_schema":
        group = (
            "css" if "unsupported_css_property" in message else
            "insert" if any(value in message for value in ("insert_element", "unsupported_tag", "Void elements")) else
            "schema"
        )
    if group is None:
        return None
    return {
        "category": group,
        "failure_code": code,
        "error": message,
        "guidance": GUIDANCE[group],
    }
