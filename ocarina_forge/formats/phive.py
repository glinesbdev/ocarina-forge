"""Read Havok 2025.2 collision used by terrain fitting and by a moved actor.

`read_mesh_shape` is the actor-local `hknpMeshShape` in a `.bphsh`. The game
does not use that file for a baked static. The bake is a `.bphsc` compound.
`patch_compound_transform` moves that actor's compound instances and leaves
the shape bytes alone.

The compound layout is the one published by reading files in
oot-texture-tool `phive_compound.py` (GPL-3.0, ToxicToucan). This module
does not copy that program. Breath of the Wild's `ActorInfo` (HashId,
SRTHash, shape start, shape end, 16 bytes) is an older SDK and is not the
layout read here. No retail `.bphsc` was opened in this repo. A file that
does not match fails closed.
"""

import math
import struct


def _chunks(payload):
    offset = 0
    while offset + 8 <= len(payload):
        size = struct.unpack_from(">I", payload, offset)[0] & 0x3fffffff
        if size < 8 or size > len(payload) - offset:
            raise ValueError("Invalid Havok chunk length")
        yield bytes(payload[offset + 4:offset + 8]), payload[offset + 8:offset + size]
        offset += size


def _type_names(types):
    strings = bytes(types[b"TST1"]).split(b"\0")
    names = [None]
    catalog = iter(types[b"TNA1"])

    def varint():
        first = next(catalog)
        extra = 0
        while extra < 5 and first & (0x80 >> extra):
            extra += 1
        if extra >= 5:
            raise ValueError("Unsupported Havok type index")
        value = first & (0x7f >> extra)
        for _ in range(extra):
            value = (value << 8) | next(catalog)
        return value

    for _ in range(1, varint()):
        names.append(strings[varint()])
        for _ in range(varint()):
            varint()
            varint()
    return names


def read_mesh_shape(raw: bytes) -> list[list[float]]:
    """Return actor-local triangles from a 2025.2 Phive hknpMeshShape.

    Return an empty list for other SDKs, shape classes, and malformed files.
    """
    try:
        return _read_mesh_shape(raw)
    except (ValueError, KeyError, IndexError, StopIteration, struct.error):
        return []


def _read_mesh_shape(raw):
    if not raw.startswith(b"Phive\0"):
        return []
    start = struct.unpack_from("<I", raw, 12)[0]
    size = struct.unpack_from("<I", raw, 28)[0]
    if start < 0x30 or start + size > len(raw):
        return []
    kind, tagfile = next(_chunks(memoryview(raw)[start:start + size]))
    if kind != b"TAG0":
        return []
    tags = dict(_chunks(tagfile))
    if tags[b"SDKV"] != b"20250200":
        return []

    data = tags[b"DATA"]
    names = _type_names(dict(_chunks(tags[b"TYPE"])))
    items = dict(_chunks(tags[b"INDX"]))[b"ITEM"]
    views = {(start, count): names[kind & 0xffffff]
             for kind, start, count in struct.iter_unpack("<III", items) if kind}
    if views.get((0, 1)) != b"hknpMeshShape":
        return []

    def relative_view(field, stride, name):
        offset, count = struct.unpack_from("<iI", data, field)
        start = field + offset
        if not 0 <= start <= start + count * stride <= len(data) or views.get((start, count)) != name:
            raise ValueError("Invalid Havok array")
        return start, count

    # The 2025.2 reflected layout puts the inverse quantization scale at
    # 0x50 and the section view at 0x88. Older SDKs use different offsets.
    inverse_scale = struct.unpack_from("<3f", data, 0x50)
    if not all(math.isfinite(value) and value > 0 for value in inverse_scale):
        return []
    section_type = b"hknpMeshShape::GeometrySection"
    section_stride = 0x40
    start, count = relative_view(0x88, section_stride, section_type)
    triangles = []
    for section in range(start, start + count * section_stride, section_stride):
        primitive_start, primitive_count = relative_view(section + 8, 4, section_type + b"::Primitive")
        vertex_start, vertex_bytes = relative_view(section + 16, 1, b"hkUint8")
        origin = struct.unpack_from("<3i", data, section + 32)
        floating = origin[0] == 0x7fffffff
        vertex_format = struct.Struct("<3f" if floating else "<3H")
        vertex_end = vertex_start + vertex_bytes // vertex_format.size * vertex_format.size
        points = list(vertex_format.iter_unpack(data[vertex_start:vertex_end]))
        if floating:
            if any(not math.isfinite(value) for point in points for value in point):
                return []
        else:
            points = [tuple((value + bias) * scale for value, bias, scale in zip(point, origin, inverse_scale))
                      for point in points]
        primitives = data[primitive_start:primitive_start + primitive_count * 4]
        for a, b, c, d in struct.iter_unpack("4B", primitives):
            triangles.append([*points[a], *points[b], *points[c]])
            if c != d:
                triangles.append([*points[a], *points[c], *points[d]])
    return triangles


# Published static-compound header. Five little-endian u32s at +0x10.
_TABLE = 0x10
_ACTOR_STRIDE = 0x20
_BODY_STRIDE = 0x14
_INSTANCE_STRIDE = 0x40
_NO_BODY = 0xFFFFFFFF
_SDK = b"20250200"


class CompoundLayoutError(ValueError):
    """This bake is not a 2025.2 static compound the patcher will edit."""


def _walk_chunks(raw, start, end):
    offset = start
    while offset + 8 <= end:
        size = struct.unpack_from(">I", raw, offset)[0] & 0x3FFFFFFF
        if size < 8 or offset + size > end:
            raise CompoundLayoutError("Invalid Havok chunk length")
        yield raw[offset + 4:offset + 8], offset + 8, offset + size
        offset += size
    if offset != end and end - offset > 7:
        raise CompoundLayoutError("Invalid Havok chunk length")


def _compound_roots(raw):
    """Absolute offsets of each tagfile's hknpCompoundShape, SDK 20250200."""
    roots = []
    start = 0
    while True:
        at = raw.find(b"TAG0", start)
        if at < 4:
            break
        size = struct.unpack_from(">I", raw, at - 4)[0] & 0x3FFFFFFF
        chunk_at = at - 4
        chunk_end = chunk_at + size
        if size < 16 or chunk_end > len(raw):
            start = at + 4
            continue
        try:
            tags = {kind: (payload_at, payload_end) for kind, payload_at, payload_end in _walk_chunks(raw, at + 4, chunk_end)}
            if not {b"SDKV", b"DATA", b"TYPE", b"INDX"} <= set(tags):
                raise CompoundLayoutError("incomplete tagfile")
            if raw[tags[b"SDKV"][0]:tags[b"SDKV"][1]] != _SDK:
                start = chunk_end
                continue
            data_at = tags[b"DATA"][0]
            type_bytes = raw[tags[b"TYPE"][0]:tags[b"TYPE"][1]]
            indx_bytes = raw[tags[b"INDX"][0]:tags[b"INDX"][1]]
            names = _type_names(dict(_chunks(type_bytes)))
            item = dict(_chunks(indx_bytes))[b"ITEM"]
            parsed = []
            for kind, offset, count in struct.iter_unpack("<III", bytes(item)):
                kind &= 0xFFFFFF
                name = names[kind] if 0 < kind < len(names) else b""
                parsed.append((name, data_at + offset, count))
            if len(parsed) > 1 and parsed[1][0] == b"hknpCompoundShape":
                roots.append(parsed[1][1])
            start = chunk_end
        except (CompoundLayoutError, ValueError, KeyError, IndexError, StopIteration, struct.error):
            start = at + 4
    return roots


def _instances(raw, compound_at):
    if compound_at < 0 or compound_at + 0x48 > len(raw):
        raise CompoundLayoutError("Compound shape is outside the file")
    relative = struct.unpack_from("<I", raw, compound_at + 0x38)[0]
    count = struct.unpack_from("<I", raw, compound_at + 0x40)[0]
    array = compound_at + 0x38 + relative
    if count > 1_000_000 or array < 0 or array + count * _INSTANCE_STRIDE > len(raw):
        raise CompoundLayoutError("Compound instance array is outside the file")
    instances = []
    for index in range(count):
        offset = array + index * _INSTANCE_STRIDE
        quat = struct.unpack_from("<4f", raw, offset)
        position = struct.unpack_from("<3f", raw, offset + 0x10)
        scale = struct.unpack_from("<3f", raw, offset + 0x20)
        shape = struct.unpack_from("<Q", raw, offset + 0x30)[0]
        if not all(math.isfinite(value) for value in (*quat, *position, *scale)):
            raise CompoundLayoutError("Compound instance has a non-finite transform")
        instances.append({
            "offset": offset,
            "quat": quat,
            "position": position,
            "scale": scale,
            "shape": shape,
        })
    return instances


def _parse_compound(raw: bytes):
    if not raw.startswith(b"Phive"):
        raise CompoundLayoutError("not a Phive file")
    if len(raw) < 0x24:
        raise CompoundLayoutError("Phive header is too short")
    actor_at, actor_end, _middle, body_at, body_end = struct.unpack_from("<5I", raw, _TABLE)
    if not (0 <= actor_at <= actor_end <= len(raw) and 0 <= body_at <= body_end <= len(raw)):
        raise CompoundLayoutError("actor or body table is outside the file")
    actors = []
    offset = actor_at
    while offset + _ACTOR_STRIDE <= actor_end:
        first, last, hash_value = struct.unpack_from("<IIQ", raw, offset)
        actors.append({"offset": offset, "first": first, "last": last, "hash": hash_value})
        offset += _ACTOR_STRIDE
    bodies = []
    offset = body_at
    while offset + _BODY_STRIDE <= body_end:
        instance = struct.unpack_from("<H", raw, offset + 0x0A)[0]
        bodies.append({"offset": offset, "instance": instance})
        offset += _BODY_STRIDE
    compounds = []
    for root in _compound_roots(raw):
        compounds.append(_instances(raw, root))
    return actors, bodies, compounds


def _norm_quat(quat):
    if not all(math.isfinite(value) for value in quat):
        raise CompoundLayoutError("quaternion is not finite")
    length = math.sqrt(sum(value * value for value in quat))
    if length < 1e-8:
        raise CompoundLayoutError("quaternion is zero")
    return tuple(value / length for value in quat)


def _euler_to_quat(rx, ry, rz):
    """Extrinsic XYZ, the same composition as a placement Rotate (Rz · Ry · Rx)."""
    cx, sx = math.cos(rx * 0.5), math.sin(rx * 0.5)
    cy, sy = math.cos(ry * 0.5), math.sin(ry * 0.5)
    cz, sz = math.cos(rz * 0.5), math.sin(rz * 0.5)
    return _norm_quat((
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    ))


def _eye():
    return [[1.0 if col == row else 0.0 for col in range(4)] for row in range(4)]


def _mul(left, right):
    return [[sum(left[row][k] * right[k][col] for k in range(4)) for col in range(4)] for row in range(4)]


def _translation(x, y, z):
    matrix = _eye()
    matrix[0][3], matrix[1][3], matrix[2][3] = x, y, z
    return matrix


def _scale_matrix(x, y, z):
    matrix = _eye()
    matrix[0][0], matrix[1][1], matrix[2][2] = x, y, z
    return matrix


def _quat_matrix(quat):
    x, y, z, w = _norm_quat(quat)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    matrix = _eye()
    matrix[0][0] = 1 - 2 * (yy + zz)
    matrix[0][1] = 2 * (xy - wz)
    matrix[0][2] = 2 * (xz + wy)
    matrix[1][0] = 2 * (xy + wz)
    matrix[1][1] = 1 - 2 * (xx + zz)
    matrix[1][2] = 2 * (yz - wx)
    matrix[2][0] = 2 * (xz - wy)
    matrix[2][1] = 2 * (yz + wx)
    matrix[2][2] = 1 - 2 * (xx + yy)
    return matrix


def _trs(translate, euler, scale):
    rotation = _quat_matrix(_euler_to_quat(*euler))
    return _mul(_mul(_translation(*translate), rotation), _scale_matrix(*scale))


def _instance_matrix(position, quat, scale):
    return _mul(_mul(_translation(*position), _quat_matrix(quat)), _scale_matrix(*scale))


def _column_lengths(matrix):
    lengths = []
    for col in range(3):
        length = math.sqrt(sum(matrix[row][col] ** 2 for row in range(3)))
        if length < 1e-8 or not math.isfinite(length):
            raise CompoundLayoutError("scale is zero")
        lengths.append(length)
    return tuple(lengths)


def _invert_trs(matrix):
    lengths = _column_lengths(matrix)
    rotation = _eye()
    for col, length in enumerate(lengths):
        for row in range(3):
            rotation[row][col] = matrix[row][col] / length
    inverse_scale = _scale_matrix(*(1.0 / length for length in lengths))
    inverse_rotation = _eye()
    for row in range(3):
        for col in range(3):
            inverse_rotation[row][col] = rotation[col][row]
    inverse_translation = _translation(-matrix[0][3], -matrix[1][3], -matrix[2][3])
    return _mul(_mul(inverse_scale, inverse_rotation), inverse_translation)


def _matrix_to_quat(matrix):
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2
        quat = (
            (matrix[2][1] - matrix[1][2]) / scale,
            (matrix[0][2] - matrix[2][0]) / scale,
            (matrix[1][0] - matrix[0][1]) / scale,
            0.25 * scale,
        )
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2
        quat = (
            0.25 * scale,
            (matrix[0][1] + matrix[1][0]) / scale,
            (matrix[0][2] + matrix[2][0]) / scale,
            (matrix[2][1] - matrix[1][2]) / scale,
        )
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2
        quat = (
            (matrix[0][1] + matrix[1][0]) / scale,
            0.25 * scale,
            (matrix[1][2] + matrix[2][1]) / scale,
            (matrix[0][2] - matrix[2][0]) / scale,
        )
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2
        quat = (
            (matrix[0][2] + matrix[2][0]) / scale,
            (matrix[1][2] + matrix[2][1]) / scale,
            0.25 * scale,
            (matrix[1][0] - matrix[0][1]) / scale,
        )
    return _norm_quat(quat)


def _matrix_to_instance(matrix):
    lengths = _column_lengths(matrix)
    rotation = _eye()
    for col, length in enumerate(lengths):
        for row in range(3):
            rotation[row][col] = matrix[row][col] / length
    position = (matrix[0][3], matrix[1][3], matrix[2][3])
    return position, _matrix_to_quat(rotation), lengths


def _pose_numbers(pose):
    translate = tuple(float(value) for value in pose["translate"])
    rotate = tuple(float(value) for value in pose["rotate"])
    scale = tuple(float(value) for value in pose["scale"])
    if not all(math.isfinite(value) for value in (*translate, *rotate, *scale)):
        raise CompoundLayoutError("pose is not finite")
    if any(value == 0 or not math.isfinite(value) for value in scale):
        raise CompoundLayoutError("scale component is zero")
    return translate, rotate, scale


def _close_vec(left, right, tol):
    return max(abs(a - b) for a, b in zip(left, right)) <= tol


def _quat_close(left, right):
    a = _norm_quat(left)
    b = _norm_quat(right)
    return abs(sum(x * y for x, y in zip(a, b))) > 0.9999


def _matches_actor(instance, translate, euler, scale):
    if any(value <= 0 for value in instance["scale"]) or any(value <= 0 for value in scale):
        return False
    return (
        _close_vec(instance["position"], translate, 1e-3)
        and _close_vec(instance["scale"], scale, 1e-4)
        and _quat_close(instance["quat"], _euler_to_quat(*euler))
    )


def _pick_instance(compounds, index, actor_position):
    hits = []
    for instances in compounds:
        if index < len(instances) and instances[index]["shape"]:
            hits.append(instances[index])
    if not hits:
        raise CompoundLayoutError(f"body instance {index} has no shape in a compound")
    if len(hits) == 1:
        return hits[0]
    near = [item for item in hits if _close_vec(item["position"], actor_position, 0.01)]
    if len(near) == 1:
        return near[0]
    raise CompoundLayoutError(
        f"body instance {index} is in {len(hits)} compounds and the actor position does not pick one"
    )


def _new_instance(instance, old_pose, new_pose):
    old_t, old_e, old_s = _pose_numbers(old_pose)
    new_t, new_e, new_s = _pose_numbers(new_pose)
    if any(value <= 0 for value in (*old_s, *new_s, *instance["scale"])):
        raise CompoundLayoutError("a non-positive scale is left unchanged")
    if _matches_actor(instance, old_t, old_e, old_s):
        return new_t, _euler_to_quat(*new_e), new_s
    moved = _mul(
        _mul(_trs(new_t, new_e, new_s), _invert_trs(_trs(old_t, old_e, old_s))),
        _instance_matrix(instance["position"], instance["quat"], instance["scale"]),
    )
    return _matrix_to_instance(moved)


def patch_compound_transform(raw: bytes, actor_hash: int, old_pose: dict, new_pose: dict) -> bytes | None:
    """Move one actor's compound instances. Return None when this tile has no bodies for it.

    The actor record (hash and shape range) and every byte that is not an
    instance quaternion, position, or scale are copied through. The shape
    pointer at instance +0x30 is one of those bytes.
    """
    actors, bodies, compounds = _parse_compound(raw)
    hash_value = int(actor_hash) & 0xFFFFFFFFFFFFFFFF
    matches = [actor for actor in actors if actor["hash"] == hash_value]
    if not matches:
        return None
    if len(matches) != 1:
        raise CompoundLayoutError(f"hash 0x{hash_value:016x} is in the actor table more than once")
    actor = matches[0]
    if actor["first"] == _NO_BODY:
        return None
    if actor["last"] < actor["first"] or actor["last"] >= len(bodies):
        raise CompoundLayoutError("shape range is outside the body table")
    if not compounds:
        raise CompoundLayoutError("actor is in the table and the file has no hknpCompoundShape")
    chosen = []
    seen = set()
    for body in bodies[actor["first"]:actor["last"] + 1]:
        instance = _pick_instance(compounds, body["instance"], old_pose["translate"])
        if instance["offset"] in seen:
            continue
        seen.add(instance["offset"])
        chosen.append(instance)
    if not chosen:
        raise CompoundLayoutError("actor shape range names no instance")

    out = bytearray(raw)
    allowed = set()
    for instance in chosen:
        position, quat, scale = _new_instance(instance, old_pose, new_pose)
        offset = instance["offset"]
        struct.pack_into("<4f", out, offset, *quat)
        struct.pack_into("<3f", out, offset + 0x10, *position)
        struct.pack_into("<3f", out, offset + 0x20, *scale)
        allowed.update(range(offset, offset + 16))
        allowed.update(range(offset + 0x10, offset + 0x10 + 12))
        allowed.update(range(offset + 0x20, offset + 0x20 + 12))
    for index, (before, after) in enumerate(zip(raw, out)):
        if before != after and index not in allowed:
            raise CompoundLayoutError("a byte outside the instance transform changed")
    if len(out) != len(raw):
        raise CompoundLayoutError("the bake changed size")
    return bytes(out)
