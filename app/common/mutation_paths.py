from typing import Any


PROTECTED_MUTATION_PATHS = {
    "meta",
    "meta.industry",
    "meta.target",
    "meta.goal",
    "meta.language",
}


def normalize_mutation_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("mutation path는 비어 있을 수 없습니다.")

    if normalized.startswith("$."):
        normalized = normalized[2:]
    elif normalized.startswith("/"):
        normalized = ".".join(
            segment.replace("~1", "/").replace("~0", "~")
            for segment in normalized.strip("/").split("/")
            if segment
        )

    if "." not in normalized:
        raise ValueError("mutation path는 dot path 또는 JSON Pointer 형식이어야 합니다.")
    if _is_protected_path(normalized):
        raise ValueError(f"통제 변수는 mutation으로 변경할 수 없습니다: {normalized}")

    return normalized


def _is_protected_path(path: str) -> bool:
    return any(
        path == protected_path or path.startswith(f"{protected_path}.")
        for protected_path in PROTECTED_MUTATION_PATHS
    )


def set_mutation_path(target: dict[str, Any], path: str, value: Any) -> None:
    normalized_path = normalize_mutation_path(path)
    keys = normalized_path.split(".")
    current: Any = target

    for key in keys[:-1]:
        current = _get_next_value(current, key, normalized_path)

    _set_value(current, keys[-1], value, normalized_path)


def _get_next_value(current: Any, key: str, original_path: str) -> Any:
    if isinstance(current, dict):
        if key not in current:
            raise ValueError(f"mutation path를 찾을 수 없습니다: {original_path}")
        return current[key]

    if isinstance(current, list):
        index = _parse_list_index(key, original_path)
        try:
            return current[index]
        except IndexError as index_err:
            raise ValueError(f"mutation list index가 범위를 벗어났습니다: {original_path}") from index_err

    raise ValueError(f"mutation path를 적용할 수 없습니다: {original_path}")


def _set_value(current: Any, key: str, value: Any, original_path: str) -> None:
    if isinstance(current, dict):
        if key not in current:
            raise ValueError(f"mutation path를 찾을 수 없습니다: {original_path}")
        current[key] = value
        return

    if isinstance(current, list):
        index = _parse_list_index(key, original_path)
        try:
            current[index] = value
        except IndexError as index_err:
            raise ValueError(f"mutation list index가 범위를 벗어났습니다: {original_path}") from index_err
        return

    raise ValueError(f"mutation path를 적용할 수 없습니다: {original_path}")


def _parse_list_index(key: str, original_path: str) -> int:
    if not key.isdigit():
        raise ValueError(f"mutation list index가 숫자가 아닙니다: {original_path}")
    return int(key)
