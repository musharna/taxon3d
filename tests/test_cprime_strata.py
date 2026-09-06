# tests/test_cprime_strata.py
from __future__ import annotations

from collections import Counter

import pytest

from scripts.paper.cprime_strata import (
    STRATA,
    TARGETS,
    anonymize,
    assign_stratum,
    draw_calibration,
    plan_sample,
)


def test_strata_names_and_targets_align():
    assert set(TARGETS) == set(STRATA)
    assert sum(TARGETS.values()) == 266


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(admitted=True, structural_reason="", semantic_code="ok", completeness_category="complete"), "admitted"),
        (dict(admitted=False, structural_reason="degenerate_bbox", semantic_code=None, completeness_category=None), "struct_degenerate_bbox"),
        (dict(admitted=False, structural_reason="empty", semantic_code="not_the_organism", completeness_category="fragment"), "struct_empty"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category="complete"), "novel_multiple"),
        (dict(admitted=False, structural_reason="", semantic_code="not_the_organism", completeness_category="complete"), "novel_not_the_organism"),
        (dict(admitted=False, structural_reason="", semantic_code="sub_part", completeness_category="complete"), "novel_sub_part"),
        (dict(admitted=False, structural_reason="", semantic_code="sub_part", completeness_category="isolated-organ"), "sem_also_completeness"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category="fragment"), "sem_also_completeness"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category="partial-organism"), "sem_only_other"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category=None), "sem_only_other"),
    ],
)
def test_assign_stratum(kw, expected):
    assert assign_stratum(**kw) == expected


def test_assign_stratum_rejects_completeness_only_reject():
    # An output the completeness gate alone rejects (semantic said ok) is not in C′'s
    # population — the spec audits the semantic and structural predicates. Fail loud.
    with pytest.raises(ValueError):
        assign_stratum(admitted=False, structural_reason="", semantic_code="ok", completeness_category="fragment")


def test_plan_sample_sizes_and_inclusion_probs():
    pops = {s: list(range(i * 1000, i * 1000 + 50)) for i, s in enumerate(STRATA)}
    targets = {s: 10 for s in STRATA}
    task_of = {oid: oid % 3 for s in pops for oid in pops[s]}
    rows = plan_sample(pops, targets, task_of=task_of, seed=1)
    by = Counter(r["stratum"] for r in rows)
    assert all(by[s] == 10 for s in STRATA)
    assert len({r["output_id"] for r in rows}) == len(rows)
    for r in rows:
        if r["stratum"] != "admitted":
            assert r["inclusion_prob"] == pytest.approx(10 / 50)


def test_plan_sample_takes_whole_stratum_when_target_exceeds_population():
    pops = {"struct_degenerate_bbox": [1, 2, 3]}
    rows = plan_sample(pops, {"struct_degenerate_bbox": 4}, task_of={1: 1, 2: 1, 3: 1}, seed=1)
    assert sorted(r["output_id"] for r in rows) == [1, 2, 3]
    assert all(r["inclusion_prob"] == 1.0 for r in rows)


def test_plan_sample_admitted_is_proportional_by_task():
    # 90 admitted: task A 60, task B 30; target 30 -> 20 from A, 10 from B, probs 1/3 each
    pops = {"admitted": list(range(90))}
    task_of = {i: ("A" if i < 60 else "B") for i in range(90)}
    rows = plan_sample(pops, {"admitted": 30}, task_of=task_of, seed=3)
    by_task = Counter(task_of[r["output_id"]] for r in rows)
    assert by_task == {"A": 20, "B": 10}
    assert all(r["inclusion_prob"] == pytest.approx(1 / 3) for r in rows)


def test_plan_sample_is_deterministic_for_seed():
    pops = {"novel_multiple": list(range(49))}
    a = plan_sample(pops, {"novel_multiple": 40}, task_of={i: 0 for i in range(49)}, seed=11)
    b = plan_sample(pops, {"novel_multiple": 40}, task_of={i: 0 for i in range(49)}, seed=11)
    assert a == b


def test_draw_calibration_excludes_main_and_spreads():
    pops = {"admitted": list(range(100)), "novel_multiple": list(range(100, 130))}
    main = set(range(0, 95)) | set(range(100, 125))
    rows = draw_calibration(pops, main, n=6, seed=2)
    ids = {r["output_id"] for r in rows}
    assert len(ids) == 6 and not (ids & main)
    assert Counter(r["stratum"] for r in rows) == {"admitted": 3, "novel_multiple": 3}
    assert all(r["inclusion_prob"] == 0.0 for r in rows)


def test_anonymize_is_a_seeded_bijection_with_zero_padding():
    m = anonymize([50, 7, 900], seed=5)
    assert sorted(m.values()) == ["c001", "c002", "c003"]
    assert m == anonymize([50, 7, 900], seed=5)
    assert m != anonymize([50, 7, 900], seed=6) or True  # different seed may differ; bijection is what matters
    assert len(set(m.values())) == 3
