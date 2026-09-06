# tests/test_cprime_agreement.py
from __future__ import annotations

import pytest

from scripts.paper.cprime_agreement import (
    ht_rate,
    krippendorff_alpha_nominal,
    per_class_agreement,
    raw_agreement,
    wilson,
)


def test_alpha_perfect_agreement_is_one():
    assert krippendorff_alpha_nominal([["a", "a"], ["b", "b"], ["a", "a"]]) == pytest.approx(1.0)


def test_alpha_hand_computed_two_raters():
    # 2 raters, 4 units: aa, bb, ab, ba. N=8 values, n_a=n_b=4.
    # Do = (1/8) * [0 + 0 + 2 + 2] = 0.5 ; De = 2*4*4 / (8*7) = 0.5714 ; alpha = 1 - 0.5/0.5714 = 0.125
    units = [["a", "a"], ["b", "b"], ["a", "b"], ["b", "a"]]
    assert krippendorff_alpha_nominal(units) == pytest.approx(0.125, abs=1e-6)


def test_alpha_ignores_missing_and_single_valued_units():
    with_missing = [["a", "a", None], ["b", "b", None], ["a", "b", None], ["b", "a", None], ["a", None, None]]
    assert krippendorff_alpha_nominal(with_missing) == pytest.approx(0.125, abs=1e-6)


def test_alpha_none_when_nothing_pairable():
    assert krippendorff_alpha_nominal([["a", None], [None, "b"]]) is None


def test_raw_and_per_class_agreement():
    a = ["ok", "ok", "multiple", "sub_part", "ok"]
    b = ["ok", "multiple", "multiple", "ok", "ok"]
    assert raw_agreement(a, b) == pytest.approx(3 / 5)
    pc = per_class_agreement(a, b)
    # ok: either said ok in units 0,1,3,4 (4); both in 0,4 (2) -> 0.5
    assert pc["ok"] == pytest.approx(0.5)
    assert pc["multiple"] == pytest.approx(0.5)  # units 1,2 ; both in 2
    assert pc["sub_part"] == pytest.approx(0.0)


def test_wilson_matches_known_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4901, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_ht_rate_reweights_by_inclusion_prob():
    # Two strata: A (prob 0.5, 2 items, 1 positive) and B (prob 0.1, 1 item, 1 positive).
    # Weighted positives = 1*2 + 1*10 = 12 ; weighted total = 2*2 + 1*10 = 14 -> 12/14
    items = [
        {"inclusion_prob": 0.5, "pos": True},
        {"inclusion_prob": 0.5, "pos": False},
        {"inclusion_prob": 0.1, "pos": True},
    ]
    r = ht_rate(items, numerator=lambda i: i["pos"], denominator=lambda i: True)
    assert r == pytest.approx(12 / 14)
    assert ht_rate([], numerator=lambda i: True, denominator=lambda i: True) is None


def test_ht_rate_skips_zero_inclusion_prob_items():
    items = [{"inclusion_prob": 0.0, "pos": True}, {"inclusion_prob": 1.0, "pos": False}]
    assert ht_rate(items, numerator=lambda i: i["pos"], denominator=lambda i: True) == pytest.approx(0.0)
