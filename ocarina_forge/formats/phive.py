"""Read Havok 2025.2 collision used by terrain fitting and by a moved actor.

`read_mesh_shape` is the actor-local `hknpMeshShape` in a `.bphsh`. The game
does not use that file for a baked static. The bake is a `.bphsc` compound.
`patch_compound_transform` moves that actor's compound instances, refits the
compound broadphase, and leaves the shape bytes alone.

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
_NODE_STRIDE = 128
_NO_BODY = 0xFFFFFFFF
_SDK = b"20250200"
_SIMD_NODE = b"hkcdSimdTreeNamespace::Node"


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


def _is_simd_node(name):
    return name == _SIMD_NODE or name.startswith(_SIMD_NODE + b"<")


def _compound_roots(raw):
    """Each tagfile's hknpCompoundShape and its SIMD-node views, SDK 20250200."""
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
                trees = [(start, count) for name, start, count in parsed if _is_simd_node(name)]
                roots.append({"at": parsed[1][1], "trees": trees})
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
            "index": index,
            "quat": quat,
            "position": position,
            "scale": scale,
            "shape": shape,
        })
    return instances, array + count * _INSTANCE_STRIDE


def _exclusive_tree(tree, trees):
    """Keep this array from running into the next shape's node array."""
    start, count = tree
    end = start + count * _NODE_STRIDE
    for other_start, _other_count in trees:
        if other_start <= start or other_start >= end:
            continue
        count = (other_start - start) // _NODE_STRIDE
        end = start + count * _NODE_STRIDE
    if count <= 0:
        raise CompoundLayoutError("SIMD tree is empty")
    return (start, count)


def _bind_tree(array_end, trees):
    """The node array that starts at this compound's instance array, if the tagfile has one.

    Other ``hkcdSimdTreeNamespace::Node`` arrays in the same tagfile belong to
    other shapes. Their child indices are local to those arrays, so they are
    not part of this walk. A count that would run into the next array is cut
    at that boundary.
    """
    exact = [tree for tree in trees if tree[0] == array_end]
    if len(exact) > 1:
        raise CompoundLayoutError("more than one SIMD tree starts at the instance array")
    if len(exact) == 1:
        return _exclusive_tree(exact[0], trees)
    padded = [tree for tree in trees if 0 < tree[0] - array_end <= 16]
    if len(padded) == 1:
        return _exclusive_tree(padded[0], trees)
    return None


def compound_actor_spans(raw: bytes) -> dict[int, tuple[int, int]] | None:
    """Map each actor-table hash to ``(first body, last body)``.

    ``None`` when the header is not a Phive actor table. ``first == last ==
    0xFFFFFFFF`` is a row with no body (the Kokiri fern
    ``Land_Kokiri_Zenmai_A_02``). A hash in this table is not, by itself,
    baked collision. The 16 bytes at record ``+0x10`` start with the u32
    ``SRTHash``. This reader does not return that word, and the patcher does
    not write the tail.
    """
    try:
        if not raw.startswith(b"Phive") or len(raw) < 0x24:
            return None
        actor_at, actor_end, _middle, body_at, body_end = struct.unpack_from("<5I", raw, _TABLE)
        if not (0 <= actor_at <= actor_end <= len(raw) and 0 <= body_at <= body_end <= len(raw)):
            return None
        spans: dict[int, tuple[int, int]] = {}
        offset = actor_at
        while offset + _ACTOR_STRIDE <= actor_end:
            first, last, hash_value = struct.unpack_from("<IIQ", raw, offset)
            spans[hash_value] = (first, last)
            offset += _ACTOR_STRIDE
        return spans
    except struct.error:
        return None


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
        instances, array_end = _instances(raw, root["at"])
        tree = _bind_tree(array_end, root["trees"])
        for instance in instances:
            instance["tree"] = tree
            instance["simd_present"] = bool(root["trees"])
        compounds.append(instances)
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


def _slot_aabb(buf, node_at, slot):
    def axis(base):
        return struct.unpack_from("<f", buf, node_at + base + 4 * slot)[0]

    return (axis(0x00), axis(0x20), axis(0x40)), (axis(0x10), axis(0x30), axis(0x50))


_NODE_IS_LEAF = 0x1
_NODE_IS_FREE = 0x2
_NO_INDEX = 0xFFFFFFFF


def _u32(buf, at):
    return struct.unpack_from("<I", buf, at)[0]


def _node_flag_mode(buf, tree):
    """True when the tail is a node flag, not four 0/1 per-lane leaf words.

    A per-lane leaf mask stores 0 or 1 in each word at ``+0x70``. The compound
    tree stores filter bytes, a flags byte, ``m_parent``, and ``m_numLeaves``
    there, so at least one of those words is neither 0 nor 1.
    """
    start, count = tree
    for node in range(count):
        words = struct.unpack_from("<4I", buf, start + node * _NODE_STRIDE + 0x70)
        if any(word not in (0, 1) for word in words):
            return True
    return False


def _child_base(buf, tree, node_flag):
    """0 when ``m_data`` stores a node index, 1 when it stores that index plus one.

    ``m_parent`` at ``+0x78`` is what decides. Empty lanes store 0, so a stored
    0 is not a vote for node 0. A per-lane mask has no parent word and stays
    0-based.
    """
    if not node_flag:
        return 0
    start, count = tree
    zero_based = one_based = 0
    for node in range(count):
        if node == 0:
            continue
        node_at = start + node * _NODE_STRIDE
        parent = _u32(buf, node_at + 0x78)
        if parent >= count:
            continue
        pdata = struct.unpack_from("<4I", buf, start + parent * _NODE_STRIDE + 0x60)
        if node in pdata:
            zero_based += 1
        if (node + 1) in pdata:
            one_based += 1
    return 1 if one_based > zero_based else 0


def _classify_lane(buf, node_at, slot, node_flag, base, count):
    """``empty``, ``leaf`` (instance index), or ``child`` (node index).

    An inverted box is empty. On an internal node, ``0`` and ``0xFFFFFFFF``
    are empty sentinels, not a pointer at node 0. On a leaf node,
    ``0xFFFFFFFF`` is an empty lane and any other ``m_data`` word is the
    instance index. The leaf bit is per lane when the tail is a 0/1 mask,
    and ``NODE_IS_LEAF`` in the flags byte otherwise.
    """
    mins, maxs = _slot_aabb(buf, node_at, slot)
    data = _u32(buf, node_at + 0x60 + 4 * slot)
    if _slot_empty(mins, maxs):
        return "empty", None
    if node_flag:
        flags = buf[node_at + 0x74]
        if flags & _NODE_IS_FREE:
            return "empty", None
        if flags & _NODE_IS_LEAF:
            if data == _NO_INDEX:
                return "empty", None
            return "leaf", data
        if data in (0, _NO_INDEX):
            return "empty", None
        child = data - base
        if child < 0 or child >= count:
            return "empty", None
        return "child", child
    if _u32(buf, node_at + 0x70 + 4 * slot):
        if data == _NO_INDEX:
            return "empty", None
        return "leaf", data
    if data in (0, _NO_INDEX) or data >= count:
        return "empty", None
    return "child", data


def _slot_empty(mins, maxs):
    if not all(math.isfinite(value) for value in (*mins, *maxs)):
        return True
    return mins[0] > maxs[0] or mins[1] > maxs[1] or mins[2] > maxs[2]


def _check_tree(buf, tree):
    start, count = tree
    if count > 1_000_000 or start < 0 or start + count * _NODE_STRIDE > len(buf):
        raise CompoundLayoutError("SIMD tree is outside the file")


def _node_at(tree, index):
    start, count = tree
    if index < 0 or index >= count:
        raise CompoundLayoutError("SIMD tree node index is outside the array")
    return start + index * _NODE_STRIDE


def _write_slot_aabb(out, node_at, slot, mins, maxs, allowed):
    for base, value in (
        (0x00, mins[0]), (0x10, maxs[0]),
        (0x20, mins[1]), (0x30, maxs[1]),
        (0x40, mins[2]), (0x50, maxs[2]),
    ):
        at = node_at + base + 4 * slot
        struct.pack_into("<f", out, at, float(value))
        allowed.update(range(at, at + 4))


def _transform_point(matrix, point):
    x, y, z = point
    return tuple(
        matrix[row][0] * x + matrix[row][1] * y + matrix[row][2] * z + matrix[row][3]
        for row in range(3)
    )


def _map_aabb(mins, maxs, delta):
    corners = [
        _transform_point(delta, (x, y, z))
        for x in (mins[0], maxs[0])
        for y in (mins[1], maxs[1])
        for z in (mins[2], maxs[2])
    ]
    mapped_min = tuple(min(corner[axis] for corner in corners) for axis in range(3))
    mapped_max = tuple(max(corner[axis] for corner in corners) for axis in range(3))
    if not all(math.isfinite(value) for value in (*mapped_min, *mapped_max)):
        raise CompoundLayoutError("refit AABB is not finite")
    return mapped_min, mapped_max


def _contains_point(mins, maxs, point, tol=1e-3):
    return all(mins[axis] - tol <= point[axis] <= maxs[axis] + tol for axis in range(3))


def _lane_context(buf, tree):
    _check_tree(buf, tree)
    node_flag = _node_flag_mode(buf, tree)
    return node_flag, _child_base(buf, tree, node_flag)


def _find_leaf_slots(buf, tree, instance_index, old_position, node_flag, base):
    """Leaf lanes for one instance: payload index first, then the tightest old box."""
    start, count = tree
    by_payload = []
    by_box = []
    for node in range(count):
        node_at = start + node * _NODE_STRIDE
        for slot in range(4):
            kind, value = _classify_lane(buf, node_at, slot, node_flag, base, count)
            if kind != "leaf":
                continue
            mins, maxs = _slot_aabb(buf, node_at, slot)
            if value == instance_index:
                by_payload.append((node, slot, mins, maxs))
            elif _contains_point(mins, maxs, old_position):
                span = tuple(maxs[axis] - mins[axis] for axis in range(3))
                by_box.append((node, slot, mins, maxs, abs(span[0] * span[1] * span[2])))
    if by_payload:
        return by_payload
    if not by_box:
        return []
    by_box.sort(key=lambda item: item[4])
    tight = [item for item in by_box if item[4] <= by_box[0][4] + 1e-6]
    if len(tight) != 1:
        return []
    node, slot, mins, maxs, _volume = tight[0]
    return [(node, slot, mins, maxs)]


def _parent_links(buf, tree, node_flag, base):
    """Child node index to the one internal slot that names it.

    Empty lanes, leaf lanes, and the sentinels ``0`` and ``0xFFFFFFFF`` are
    not children. Two internal lanes that name the same real child still fail.
    """
    start, count = tree
    parents = {}
    for node in range(count):
        node_at = start + node * _NODE_STRIDE
        for slot in range(4):
            kind, child = _classify_lane(buf, node_at, slot, node_flag, base, count)
            if kind != "child":
                continue
            if child in parents:
                raise CompoundLayoutError("SIMD tree node has two parents")
            parents[child] = (node, slot)
    return parents


def _union_slots(buf, node_at, node_flag, base, count):
    mins = None
    maxs = None
    for slot in range(4):
        kind, _value = _classify_lane(buf, node_at, slot, node_flag, base, count)
        if kind == "empty":
            continue
        slot_min, slot_max = _slot_aabb(buf, node_at, slot)
        if mins is None:
            mins, maxs = slot_min, slot_max
            continue
        mins = tuple(min(mins[axis], slot_min[axis]) for axis in range(3))
        maxs = tuple(max(maxs[axis], slot_max[axis]) for axis in range(3))
    return mins, maxs


def _refit_simd_tree(out, tree, updates, allowed):
    """Move each instance's leaf, then set every parent slot to its child's union.

    ``updates`` is ``(instance index, old matrix, new matrix, old position)``.
    A leaf is the lane whose payload is that index, or the unique tightest leaf
    whose box contains the old instance position. The new box is the min/max of
    the old leaf's eight corners through ``new * inverse(old)``. Only those six
    SoA floats are written. The eight u32s, and every other lane, stay.
    Empty internal lanes store ``0`` or ``0xFFFFFFFF`` and are not children.
    The walk is this compound's node array only.
    """
    node_flag, base = _lane_context(out, tree)
    parents = _parent_links(out, tree, node_flag, base)
    dirty = set()
    for instance_index, old_matrix, new_matrix, old_position in updates:
        leaves = _find_leaf_slots(out, tree, instance_index, old_position, node_flag, base)
        if not leaves:
            raise CompoundLayoutError(f"instance {instance_index} has no broadphase leaf")
        delta = _mul(new_matrix, _invert_trs(old_matrix))
        for node, slot, mins, maxs in leaves:
            if parents and node not in parents:
                raise CompoundLayoutError("SIMD tree leaf has no parent")
            mapped_min, mapped_max = _map_aabb(mins, maxs, delta)
            _write_slot_aabb(out, _node_at(tree, node), slot, mapped_min, mapped_max, allowed)
            dirty.add(node)
    guard = 0
    while dirty:
        guard += 1
        if guard > tree[1] + 1:
            raise CompoundLayoutError("SIMD tree parent walk did not reach the root")
        nxt = set()
        for node in dirty:
            parent = parents.get(node)
            if parent is None:
                continue
            parent_node, parent_slot = parent
            union_min, union_max = _union_slots(out, _node_at(tree, node), node_flag, base, tree[1])
            if union_min is None:
                raise CompoundLayoutError("SIMD tree child has no bounds")
            _write_slot_aabb(out, _node_at(tree, parent_node), parent_slot, union_min, union_max, allowed)
            nxt.add(parent_node)
        dirty = nxt


def patch_compound_transform(raw: bytes, actor_hash: int, old_pose: dict, new_pose: dict) -> bytes | None:
    """Move one actor's compound instances. Return None when this tile has no bodies for it.

    Writes the instance quaternion, position, and scale, then refits that
    instance's leaf in ``hkcdSimdTreeNamespace::Node`` and the parent slots
    above it. A tagfile with no such array is left as an instance-only edit.
    A tagfile that has the array and no leaf for a moved instance fails closed.
    The shape pointer at instance +0x30, the actor-table tail, ``SRTHash``,
    and per-mesh ``hknpAabb8TreeNode`` arrays are not written.
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
    refits = {}
    for instance in chosen:
        old_matrix = _instance_matrix(instance["position"], instance["quat"], instance["scale"])
        position, quat, scale = _new_instance(instance, old_pose, new_pose)
        new_matrix = _instance_matrix(position, quat, scale)
        offset = instance["offset"]
        struct.pack_into("<4f", out, offset, *quat)
        struct.pack_into("<3f", out, offset + 0x10, *position)
        struct.pack_into("<3f", out, offset + 0x20, *scale)
        allowed.update(range(offset, offset + 16))
        allowed.update(range(offset + 0x10, offset + 0x10 + 12))
        allowed.update(range(offset + 0x20, offset + 0x20 + 12))
        tree = instance["tree"]
        if tree is None:
            if instance["simd_present"]:
                raise CompoundLayoutError("SIMD tree is not tied to the instance array")
            continue
        refits.setdefault(tree, []).append((instance["index"], old_matrix, new_matrix, instance["position"]))
    for tree, updates in refits.items():
        _refit_simd_tree(out, tree, updates, allowed)
    for index, (before, after) in enumerate(zip(raw, out)):
        if before != after and index not in allowed:
            raise CompoundLayoutError("a byte outside the instance transform and the refit bounds changed")
    if len(out) != len(raw):
        raise CompoundLayoutError("the bake changed size")
    return bytes(out)
