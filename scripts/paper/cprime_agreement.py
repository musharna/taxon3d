# scripts/paper/cprime_agreement.py
"""Agreement and design-weighted rates for Study C′. Pure, stdlib only.

Krippendorff's α (nominal) follows Krippendorff (2011), "Computing Krippendorff's Alpha-
Reliability": α = 1 − D_o/D_e with D_o = (1/n) Σ_u (1/(m_u−1)) Σ_{c≠k} o_uck and
D_e = (1/(n(n−1))) Σ_{c≠k} n_c n_k over the n pairable values (units with ≥2 values)."""

from __future__ import annotations

import math
from collections import Counter
from typing import Callable


def krippendorff_alpha_nominal(units: list[list[str | None]]) -> float | None:
    pairable = [[v for v in u if v is not None] for u in units]
    pairable = [u for u in pairable if len(u) >= 2]
    n = sum(len(u) for u in pairable)
    if n < 2:
        return None
    totals: Counter = Counter()
    d_o = 0.0
    for u in pairable:
        m = len(u)
        cnt = Counter(u)
        totals.update(cnt)
        # ordered pairs (c != k) within the unit = m*(m-1) - Σ_c cnt_c*(cnt_c-1)
        disagree = m * (m - 1) - sum(c * (c - 1) for c in cnt.values())
        d_o += disagree / (m - 1)
    d_o /= n
    d_e = (n * (n - 1) - sum(c * (c - 1) for c in totals.values())) / (n * (n - 1))
    if d_e == 0:
        return 1.0
    return 1.0 - d_o / d_e


def raw_agreement(a: list[str], b: list[str]) -> float:
    if len(a) != len(b):
        raise ValueError(f"length mismatch {len(a)} vs {len(b)}")
    if not a:
        return float("nan")
    return sum(x == y for x, y in zip(a, b)) / len(a)


def per_class_agreement(a: list[str], b: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in sorted(set(a) | set(b)):
        either = [(x, y) for x, y in zip(a, b) if x == c or y == c]
        both = sum(1 for x, y in either if x == y == c)
        out[c] = both / len(either) if either else float("nan")
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def ht_rate(
    items: list[dict], *, numerator: Callable[[dict], bool], denominator: Callable[[dict], bool]
) -> float | None:
    num = den = 0.0
    for it in items:
        pi = float(it["inclusion_prob"])
        if pi <= 0:
            continue  # calibration items carry 0.0 and must not enter an estimate
        w = 1.0 / pi
        if denominator(it):
            den += w
            if numerator(it):
                num += w
    return None if den == 0 else num / den
