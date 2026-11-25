"""Decode the rupee delta written by a ``debug_cases`` boot hook.

One load adds 1, then exactly one of +2/+4/+8, then exactly one of +16/+32.
Those amounts are distinct bits, so the difference of the two counts is the path.
"""

from __future__ import annotations

from after_ocarina.errors import ModError

# Meanings measured in game: int cases are less, equal, greater against Value.
# Bool cases are inverted from the first guess: 0 is true, 1 is false.
_BITS = (
    (1, "entry +1", "the boot hook ran (+1)"),
    (2, "int case 0 (less)", "EventQueryIsGameDataInt took case 0, less than Value (+2)"),
    (4, "int case 1 (equal)", "EventQueryIsGameDataInt took case 1, equal to Value (+4)"),
    (8, "int case 2 (greater)", "EventQueryIsGameDataInt took case 2, greater than Value (+8)"),
    (16, "bool case 0 (true)", "EventQueryIsGameDataBool took case 0, true (+16)"),
    (32, "bool case 1 (false)", "EventQueryIsGameDataBool took case 1, false (+32)"),
)
_MASK = 63
_PATHS = {
    1 + 2 + 16: "int case 0 (less), bool case 0 (true)",
    1 + 4 + 16: "int case 1 (equal), bool case 0 (true)",
    1 + 8 + 16: "int case 2 (greater), bool case 0 (true)",
    1 + 2 + 32: "int case 0 (less), bool case 1 (false)",
    1 + 4 + 32: "int case 1 (equal), bool case 1 (false)",
    1 + 8 + 32: "int case 2 (greater), bool case 1 (false)",
}


def format_decode(before: int, after: int) -> str:
    if isinstance(before, bool) or isinstance(after, bool) or not isinstance(before, int) or not isinstance(after, int):
        raise ModError("rupee counts must be integers")
    if before < 0 or after < 0:
        raise ModError("rupee counts cannot be negative")
    delta = after - before
    lines = [f"rupees {before} -> {after} (delta {delta})"]
    if delta < 0:
        lines.append("rupees fell. Spend nothing between the two counts, then load once and save.")
        return "\n".join(lines) + "\n"
    hits = [row for row in _BITS if delta & row[0]]
    extra = delta & ~_MASK
    for _bit, _name, sentence in hits:
        lines.append(f"  {sentence}")
    if extra:
        lines.append(
            f"  extra {extra} rupees are not a probe "
            "(spent or gained outside the hook, or the wallet capped the count)"
        )
    ganon = [row for row in hits if row[0] in (2, 4, 8)]
    flag = [row for row in hits if row[0] in (16, 32)]
    path = _PATHS.get(delta)
    if path is not None and not extra:
        lines.append(f"one path: {path}.")
        lines.append(
            "Verified on a cleared save: int case 2 is greater than Value, "
            "bool case 0 is true, and bool case 1 is false."
        )
    elif delta == 1:
        lines.append("only the entry +1. The int query did not take case 0, 1, or 2.")
    elif not (delta & 1) and delta != 0:
        lines.append("the +1 entry bump is missing, so this delta is not one run of the hook.")
    elif len(ganon) != 1 or len(flag) != 1 or extra:
        lines.append(
            "not a single int case plus a single bool case. "
            "Spend nothing, and decode after one load rather than two."
        )
    lines.append(
        "table: +1 entry; int +2 case 0 less, +4 case 1 equal, +8 case 2 greater; "
        "bool +16 case 0 true, +32 case 1 false"
    )
    lines.append("whole paths: 19, 21, 25, 35, 37, 41")
    return "\n".join(lines) + "\n"
