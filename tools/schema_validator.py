"""Lightweight JSON Schema validator using stdlib only.

Supports a restricted subset: required fields, type checks, enum values,
nested objects, arrays with item schemas, minItems, maxItems, additionalProperties: false,
and string pattern validation.
Does NOT support $ref, allOf, patternProperties, or other advanced JSON Schema features.
"""
import json
import re
from pathlib import Path

_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


_UNSUPPORTED_KEYWORDS = {"$ref", "allOf", "anyOf", "oneOf", "patternProperties", "if", "then", "else"}


def validate_artifact(data: dict, schema: dict) -> list[str]:
    """Validate data against a restricted JSON Schema. Returns list of error strings.

    R3-F-17 fix: Rejects schemas that use unsupported JSON Schema features ($ref,
    allOf, etc.) with a clear error message, preventing silent incorrect validation.
    """
    errors: list[str] = []
    _check_unsupported_keywords(schema, "", errors)
    if errors:
        return errors  # Abort early — schema itself is invalid for this validator
    _validate_node(data, schema, "", errors)
    return errors


def _check_unsupported_keywords(schema: dict, path: str, errors: list[str]) -> None:
    """R3-F-17: Recursively check schema for unsupported keywords."""
    for keyword in _UNSUPPORTED_KEYWORDS:
        if keyword in schema:
            errors.append(
                f"Schema{' at ' + path if path else ''}: unsupported keyword '{keyword}'. "
                f"This validator only supports a restricted JSON Schema subset."
            )
    # Recurse into nested schemas
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            _check_unsupported_keywords(prop_schema, f"{path}.{prop_name}", errors)
    if "items" in schema and isinstance(schema["items"], dict):
        _check_unsupported_keywords(schema["items"], f"{path}[]", errors)


def _validate_node(data, schema: dict, path: str, errors: list[str]) -> None:
    # Type check
    expected_type = schema.get("type")
    if expected_type:
        py_types = _TYPE_MAP.get(expected_type)
        if py_types is None:
            errors.append(f"{path or '/'}: unknown schema type '{expected_type}'")
            return
        # JSON Schema: boolean is NOT a subtype of number/integer, but Python's
        # bool is a subclass of int. Explicitly reject booleans for numeric types.
        if expected_type in ("number", "integer") and isinstance(data, bool):
            errors.append(f"{path or '/'}: expected type '{expected_type}', got 'bool'")
            return
        if not isinstance(data, py_types):
            errors.append(f"{path or '/'}: expected type '{expected_type}', got '{type(data).__name__}'")
            return  # Skip deeper checks if type is wrong

    # Enum check
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path or '/'}: value '{data}' not in enum {schema['enum']}")

    # Pattern check (F-18: validates string format, e.g., SHA-256 hex digest)
    # F-1 fix: use re.fullmatch() instead of re.match() to anchor at both ends
    if "pattern" in schema and isinstance(data, str):
        if not re.fullmatch(schema["pattern"], data):
            errors.append(f"{path or '/'}: value does not match pattern '{schema['pattern']}'")
        # F-17 fix: semantic check for line range fields (start <= end)
        elif isinstance(data, str) and re.fullmatch(r'.+:\d+-\d+', data):
            line_part = data.rsplit(":", 1)[-1]
            parts = line_part.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                if int(parts[0]) > int(parts[1]):
                    errors.append(f"{path or '/'}: line range start ({parts[0]}) > end ({parts[1]})")

    # Object: check required fields, property schemas, and additionalProperties
    if expected_type == "object" and isinstance(data, dict):
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path}/{req}: required field missing")
        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            if key in data:
                _validate_node(data[key], prop_schema, f"{path}/{key}", errors)
        if schema.get("additionalProperties") is False:
            allowed = set(props.keys())
            for key in data:
                if key not in allowed:
                    errors.append(f"{path}/{key}: unexpected additional property")

    # Array: check minItems, maxItems, and item schemas
    if expected_type == "array" and isinstance(data, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(data) < min_items:
            errors.append(f"{path or '/'}: array has {len(data)} items, minimum is {min_items}")
        max_items = schema.get("maxItems")
        if max_items is not None and len(data) > max_items:
            errors.append(f"{path or '/'}: array has {len(data)} items, maximum is {max_items}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(data):
                _validate_node(item, item_schema, f"{path}[{i}]", errors)


def load_schema(schema_path: Path) -> dict:
    """Load a JSON Schema file. Raises on parse error."""
    with open(schema_path) as f:
        return json.load(f)
