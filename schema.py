"""Bounded strict-schema subset, checked locally as well as by the provider."""

import json
import math
from collections.abc import Mapping


class VisionError(ValueError):
    """Safe diagnostics, never raw provider responses or secret values."""


class Cancelled(VisionError):
    pass


def plain(value, depth=0):
    if depth > 16:
        raise VisionError("JSON nesting exceeds the limit.")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise VisionError("JSON object keys must be strings.")
        return {key: plain(item, depth+1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item, depth+1) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        if type(value) is float and not math.isfinite(value):
            raise VisionError("Nonfinite numbers are not accepted.")
        return value
    raise VisionError("Unsupported JSON value.")


def loads(text, limit=262144):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise VisionError("Duplicate JSON keys are not accepted.")
            result[key] = value
        return result
    try:
        if not isinstance(text, (str, bytes)) or len(text) > limit:
            raise VisionError("JSON exceeds its size limit.")
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: float('nan'))
        result = plain(result)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > limit:
            raise VisionError("JSON exceeds its size limit.")
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise VisionError("Invalid, duplicated, excessively nested or oversized JSON.") from None


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


DEFAULT_SCHEMA = object_schema({"summary": {"type": "string"},
    "criteria_met": {"type": ["boolean", "null"]}})


def validate_schema(value):
    schema = loads(json.dumps(plain(value)), 16384)
    count = [0]
    def visit(node, depth=0):
        count[0] += 1
        if depth > 6 or count[0] > 128 or not isinstance(node, dict):
            raise VisionError("Schema exceeds six levels or 128 nodes.")
        allowed = {"type", "description", "enum", "properties", "required", "additionalProperties", "items"}
        if set(node) - allowed:
            raise VisionError("Use the documented strict schema subset; references and executable patterns are not supported.")
        kind = node.get("type")
        types = kind if isinstance(kind, list) else [kind]
        if not types or any(t not in ("object", "array", "string", "number", "integer", "boolean", "null") for t in types):
            raise VisionError("Schema type is invalid.")
        if len(set(types)) != len(types) or len(types) > 2 or (len(types) == 2 and "null" not in types):
            raise VisionError("Only one base type, optionally nullable, is supported.")
        if "description" in node and (not isinstance(node["description"], str) or len(node["description"]) > 1024):
            raise VisionError("Schema descriptions must be short strings.")
        if "enum" in node:
            enum = node["enum"]
            if not isinstance(enum, list) or not 1 <= len(enum) <= 64 or any(isinstance(v, (dict, list)) for v in enum):
                raise VisionError("Schema enum must contain 1–64 scalar values.")
        if "object" in types:
            props = node.get("properties")
            required = node.get("required")
            if not isinstance(props, dict) or not props or len(props) > 64 or node.get("additionalProperties") is not False:
                raise VisionError("Objects need nonempty properties and additionalProperties:false.")
            if not isinstance(required, list) or any(not isinstance(k, str) for k in required) or len(required) != len(props) or set(required) != set(props):
                raise VisionError("All object properties must be required; use nullable fields for unknown values.")
            for key, child in props.items():
                if not key or len(key) > 128:
                    raise VisionError("Schema property names must have 1–128 characters.")
                visit(child, depth+1)
        elif set(node) & {"properties", "required", "additionalProperties"}:
            raise VisionError("Object keywords need object type.")
        if "array" in types:
            visit(node.get("items"), depth+1)
        elif "items" in node:
            raise VisionError("items requires array type.")
    visit(schema)
    if schema.get("type") != "object":
        raise VisionError("Result schema must be an object.")
    return schema


def envelope(result_schema):
    region = object_schema({key: {"type": "number"} for key in ("x", "y", "width", "height")})
    region["type"] = ["object", "null"]
    evidence = object_schema({"kind": {"type": "string", "enum": ["observation", "inference"]},
        "description": {"type": "string"}, "region": region})
    return object_schema({"result": result_schema, "evidence": {"type": "array", "items": evidence},
        "issues": {"type": "array", "items": {"type": "string"}}})


def validate_result(value, schema):
    try:
        import jsonschema
        jsonschema.Draft202012Validator(schema).validate(value)
    except ImportError:
        raise VisionError("Install jsonschema in the runtime interpreter.") from None
    except Exception:
        raise VisionError("Provider output does not satisfy the selected JSON schema.") from None
    if len(value["evidence"]) > 64 or len(value["issues"]) > 64:
        raise VisionError("Too many evidence or uncertainty entries.")
    for item in value["evidence"]:
        if not item["description"].strip() or len(item["description"]) > 4096:
            raise VisionError("Invalid evidence description.")
        region = item["region"]
        if region is not None and (any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in region.values())
            or not 0 < region["width"] <= 1 or not 0 < region["height"] <= 1
            or region["x"]+region["width"] > 1.000001 or region["y"]+region["height"] > 1.000001):
            raise VisionError("Evidence region lies outside the submitted image.")
    if any(not item.strip() or len(item) > 4096 for item in value["issues"]):
        raise VisionError("Invalid uncertainty description.")
    return value
