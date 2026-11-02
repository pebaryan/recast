"""Small, explicit JSON contracts shared by the model client and app."""

def object_schema(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


def array_schema(items, maximum=20):
    return {"type": "array", "items": items, "maxItems": maximum}


STRING = {"type": "string", "maxLength": 1200}
STYLE_SCHEMA = object_schema({
    "summary": STRING,
    "traits": array_schema(object_schema({
        "dimension": STRING, "observation": STRING, "evidence": STRING,
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    }), 8),
    "rules": array_schema(STRING, 10),
    "avoid": array_schema(STRING, 6),
})
REWRITE_SCHEMA = object_schema({"blocks": array_schema(object_schema({"id": {"type": "string", "maxLength": 20}, "text": {"type": "string", "maxLength": 5000}}), 12)})
VERIFY_SCHEMA = object_schema({
    "claims": array_schema(object_schema({"block_id": {"type": "string", "maxLength": 20}, "text": {"type": "string", "maxLength": 240}}), 16),
    "findings": array_schema(object_schema({
        "block_id": STRING,
        "type": {"type": "string", "enum": ["omission", "unsupported", "contradiction", "qualification", "attribution", "name"]},
        "severity": {"type": "string", "enum": ["warning", "error"]},
        "explanation": STRING, "source_excerpt": STRING, "output_excerpt": STRING,
    }), 20),
})


def validate(value, schema, path="response"):
    kind = schema.get("type")
    expected = {"object": dict, "array": list, "string": str}.get(kind)
    if expected and not isinstance(value, expected):
        raise ValueError(f"{path} must be {kind}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value.")
    if kind == "string" and len(value) > schema.get("maxLength", 100000):
        raise ValueError(f"{path} exceeds its length limit.")
    if kind == "object":
        missing = set(schema.get("required", [])) - value.keys()
        extra = value.keys() - schema["properties"].keys()
        if missing or (extra and not schema.get("additionalProperties", True)):
            raise ValueError(f"{path} has missing or unexpected fields: {sorted(missing | extra)}")
        for key, item in value.items():
            if key in schema["properties"]:
                validate(item, schema["properties"][key], f"{path}.{key}")
    if kind == "array":
        if len(value) > schema.get("maxItems", 1000):
            raise ValueError(f"{path} has too many items.")
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")


def validate_profile(profile):
    validate(profile, STYLE_SCHEMA)
    if len(str(profile)) > 12000 or not profile["rules"] or not profile["summary"].strip():
        raise ValueError("A style profile needs a summary and at least one writing rule, within 12,000 characters.")
