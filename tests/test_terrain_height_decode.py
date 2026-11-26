"""Heightmap decode: scale, deswizzle range recovery, and walkable tile choice."""

from ocarina_forge.formats.terrain import (
    _choose_height_decode,
    _packed_height_max,
    _plausible_height_max,
    _recovers_packed_range,
)
from ocarina_forge.grounddebug import _sample


def _grid(max_value: float, holes: int = 0) -> list[float]:
    values = [max_value * (i + 1) / 66049 for i in range(66049)]
    for i in range(holes):
        values[i] = 0.0
    return values


def test_packed_height_max_ignores_padding_and_zeros():
    import struct
    # 0x88888888 padding, zeros, and a real 0.16 height.
    block = struct.pack("<4f", 0.0, float.fromhex("-1.2p-100"), 0.163626, 0.0)
    # The tiny negative is outside 0..2 and is ignored.
    assert abs(_packed_height_max(block) - 0.163626) < 1e-9


def test_a_flat_deswizzle_is_rejected_when_the_packed_block_still_has_hills():
    packed_max = 0.1058349609375
    flat = _grid(0.003906309604644775, holes=50000)  # the bad log2=4 case
    recovered = _grid(0.16362600028514862, holes=51000)  # a log2 that kept the highs
    assert not _recovers_packed_range(flat, packed_max)
    assert _recovers_packed_range(recovered, packed_max)
    assert _choose_height_decode(flat, [recovered], packed_max) is recovered
    # A tile whose default already has the packed range stays on that layout.
    good = _grid(0.22212544083595276, holes=36000)
    assert _choose_height_decode(good, [recovered], 0.2215576171875) is good


def test_walkable_sample_prefers_main_and_skips_an_empty_fine_tile():
    report = {
        "origin": [0.0, 0.0, 0.0],
        "scale": 500.0,
        "heightScale": 800.0,
        "tiles": [
            {  # finer main tile, no usable height
                "layer": "main",
                "name": "3500000323",
                "edge": 0.25,
                "center": [0.125, 0.125],
                "path": "",
                "heights": [float("nan")] * (257 * 257),
            },
            {  # coarser main tile with a real floor at raw 0.2 -> 160 m
                "layer": "main",
                "name": "34000000C8",
                "edge": 0.5,
                "center": [0.25, 0.25],
                "path": "",
                "heights": [0.2] * (257 * 257),
            },
            {  # finer sub overlay must not win over main
                "layer": "sub",
                "name": "3500000323",
                "edge": 0.25,
                "center": [0.125, 0.125],
                "path": "",
                "heights": [0.14] * (257 * 257),
            },
        ],
    }
    # A point in the first unit-space tile: world (31.25, 31.25) is inside both.
    sample = _sample(report, 31.25, 31.25)
    drawn = sample["drawn"]
    assert drawn is not None
    assert drawn["layer"] == "main"
    assert drawn["name"] == "34000000C8"
    assert abs(drawn["worldY"] - 160.0) < 1e-9


def test_near_zero_samples_are_treated_as_holes():
    from ocarina_forge.formats.terrain import _uncover_sentinel_zeros, _HEIGHT_HOLE

    values = _uncover_sentinel_zeros([0.0, _HEIGHT_HOLE, _HEIGHT_HOLE / 2, 1e-5, 0.2, -0.0])
    assert all(v != v for v in values[:4])
    assert values[4] == 0.2
    assert values[5] != values[5]

