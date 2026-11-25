"""Tagged values so the editor can round-trip every BYML type through JSON.

JavaScript cannot hold every u64 exactly, so 64-bit numbers travel as decimal
strings. The UI sends a two-item list, ["f32", 1.5]. YAML patches use plain
scalars, or a one-key dict such as {"$u32": 2}.
"""

from __future__ import annotations

import math

from ocarina_forge.errors import BancError
from ocarina_forge.formats.byml import F64, Hash32, Hash64, S64, U32, U64

_UI_TYPES = {"null", "bool", "str", "s32", "u32", "s64", "u64", "f32", "f64", "bin", "dict", "array", "h32", "h64"}
_S32_MIN = -1 << 31
_S32_MAX = (1 << 31) - 1


def _finite(value: float):
    if math.isfinite(value):
        return float(value)
    if math.isnan(value):
        return "nan"
    return "inf" if value > 0 else "-inf"


def tag(value):
    """Encode a BYML value as a JSON-safe tagged node."""
    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["bool", bool(value)]
    if isinstance(value, U32):
        return ["u32", int(value)]
    if isinstance(value, S64):
        return ["s64", str(int(value))]
    if isinstance(value, U64):
        return ["u64", str(int(value))]
    if isinstance(value, F64):
        return ["f64", _finite(float(value))]
    if isinstance(value, (bytes, bytearray)):
        return ["bin", bytes(value).hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, int):
        return ["s32", int(value)]
    if isinstance(value, float):
        return ["f32", _finite(value)]
    if isinstance(value, Hash64):
        return ["h64", {f"{int(key) & 0xFFFFFFFFFFFFFFFF:016x}": tag(item) for key, item in value.items()}]
    if isinstance(value, Hash32):
        return ["h32", {f"{int(key) & 0xFFFFFFFF:08x}": tag(item) for key, item in value.items()}]
    if isinstance(value, dict):
        return ["dict", {str(key): tag(item) for key, item in value.items()}]
    if isinstance(value, list):
        return ["array", [tag(item) for item in value]]
    raise BancError(f"Cannot show a {type(value).__name__} in the editor.", "value")


def _decode_ui(kind: str, payload):
    if kind == "null":
        return None
    if kind == "bool":
        return bool(payload)
    if kind == "str":
        return str(payload)
    if kind == "s32":
        number = int(payload)
        if not _S32_MIN <= number <= _S32_MAX:
            raise BancError(f"{number} does not fit in a 32-bit signed integer.", "value")
        return number
    if kind == "u32":
        number = int(payload)
        if not 0 <= number <= 0xFFFFFFFF:
            raise BancError(f"{number} does not fit in a 32-bit unsigned integer.", "value")
        return U32(number)
    if kind == "s64":
        return S64(int(payload))
    if kind == "u64":
        number = int(payload)
        if number < 0:
            raise BancError("An unsigned 64-bit value cannot be negative.", "value")
        return U64(number)
    if kind == "f32":
        if isinstance(payload, str):
            raise BancError(f"Cannot use {payload!r} as a 32-bit float.", "value")
        return float(payload)
    if kind == "f64":
        if isinstance(payload, str):
            raise BancError(f"Cannot use {payload!r} as a 64-bit float.", "value")
        return F64(payload)
    if kind == "bin":
        return bytes.fromhex(payload)
    if kind == "dict":
        return {str(key): untag(item) for key, item in payload.items()}
    if kind == "array":
        return [untag(item) for item in payload]
    if kind == "h32":
        return Hash32({int(key, 16): untag(item) for key, item in payload.items()})
    if kind == "h64":
        return Hash64({int(key, 16): untag(item) for key, item in payload.items()})
    raise BancError(f"Unknown value type {kind!r}.", "value")


def untag(value):
    """Decode a UI tagged node. A plain JSON scalar is accepted for simple edits."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in _UI_TYPES:
        return _decode_ui(value[0], value[1])
    return from_yaml(value)


def from_yaml(value):
    """Decode a YAML patch value. One-key dicts {"$u32": n} keep the BYML type."""
    if isinstance(value, dict):
        if len(value) == 1:
            key, item = next(iter(value.items()))
            if key == "$u32":
                return U32(int(item))
            if key == "$s64":
                return S64(int(item))
            if key == "$u64":
                return U64(int(item))
            if key == "$f64":
                return F64(item)
            if key == "$bin":
                return bytes.fromhex(item)
            if key == "$h32":
                return Hash32({int(str(k), 16): from_yaml(v) for k, v in item.items()})
            if key == "$h64":
                return Hash64({int(str(k), 16): from_yaml(v) for k, v in item.items()})
        return {str(key): from_yaml(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_yaml(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        if _S32_MIN <= value <= _S32_MAX:
            return value
        return S64(value)
    if isinstance(value, float):
        return float(value)
    raise BancError(f"Cannot store a {type(value).__name__} from a patch.", "value")


def to_yaml(value):
    """Encode a BYML value into a YAML-friendly structure, keeping unusual types."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, U32):
        return {"$u32": int(value)}
    if isinstance(value, S64):
        return {"$s64": int(value)}
    if isinstance(value, U64):
        return {"$u64": int(value)}
    if isinstance(value, F64):
        return {"$f64": float(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"$bin": bytes(value).hex()}
    if isinstance(value, Hash32):
        return {"$h32": {f"{int(key):08x}": to_yaml(item) for key, item in value.items()}}
    if isinstance(value, Hash64):
        return {"$h64": {f"{int(key):016x}": to_yaml(item) for key, item in value.items()}}
    if isinstance(value, dict):
        return {str(key): to_yaml(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_yaml(item) for item in value]
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    raise BancError(f"Cannot write a {type(value).__name__} into a project file.", "value")


def parse_path(path: str) -> list:
    """Split `Dynamic.Links[0].Hash` into ["Dynamic", "Links", 0, "Hash"]."""
    if not path or not isinstance(path, str):
        raise BancError("A parameter path is required, for example Dynamic.IsEnable.", "value")
    parts: list = []
    buf = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if buf:
                parts.append(buf)
                buf = ""
            elif not parts:
                raise BancError(f"Empty name in parameter path {path!r}.", "value")
            index += 1
        elif char == "[":
            if buf:
                parts.append(buf)
                buf = ""
            end = path.find("]", index)
            if end < 0 or not path[index + 1 : end].isdigit():
                raise BancError(f"Bad index in parameter path {path!r}.", "value")
            parts.append(int(path[index + 1 : end]))
            index = end + 1
        else:
            buf += char
            index += 1
    if buf:
        parts.append(buf)
    if not parts:
        raise BancError(f"Empty parameter path {path!r}.", "value")
    return parts


def get_path(root, path: str):
    current = root
    for part in parse_path(path):
        current = current[part]
    return current


def set_path(root, path: str, value) -> None:
    """Set a leaf, creating dictionaries along the way. Existing leaf types are kept when `value` is a plain scalar of the same family."""
    parts = parse_path(path)
    current = root
    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                raise BancError(f"No item [{part}] on the way to {path}.", "value")
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise BancError(f"{path} goes through a value that is not a dictionary.", "value")
            if part not in current or not isinstance(current[part], (dict, list)):
                current[part] = {}
            current = current[part]
    last = parts[-1]
    stored = _coerce(current[last], value) if _has(current, last) else value
    if isinstance(last, int):
        current[last] = stored
    else:
        current[last] = stored


def _has(container, key) -> bool:
    if isinstance(key, int):
        return isinstance(container, list) and 0 <= key < len(container)
    return isinstance(container, dict) and key in container


def _coerce(existing, new):
    """Keep the BYML type of `existing` when the editor sends a plain replacement."""
    if type(existing) is type(new):
        return new
    if isinstance(existing, bool):
        return bool(new)
    if isinstance(existing, U32):
        return U32(int(new))
    if isinstance(existing, S64):
        return S64(int(new))
    if isinstance(existing, U64):
        return U64(int(new))
    if isinstance(existing, F64):
        return F64(new)
    if isinstance(existing, str):
        return str(new)
    if type(existing) is int:
        return int(new)
    if type(existing) is float and isinstance(new, (int, float)) and not isinstance(new, bool):
        return float(new)
    return new


def flatten(value, prefix: str = "") -> dict:
    """Flatten a plain patch dict into dotted paths. Tagged one-key dicts stay as leaves."""
    if isinstance(value, dict) and not _is_yaml_tag(value):
        out = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(item, child))
        return out
    return {prefix: value} if prefix else {}


def _is_yaml_tag(value: dict) -> bool:
    if len(value) != 1:
        return False
    return next(iter(value)).startswith("$")
