# scripts/paper/cprime_ingest.py
"""Turn Study C′ rater CSVs into the preregistered estimates and the go/no-go.

Analysis is fixed here BEFORE labels exist (see docs/paper/prereg-cprime.md); the numbers are
read out, not chosen. Reads the PRIVATE manifest to recover stratum + inclusion probability.

Usage:
  .venv/bin/python scripts/paper/cprime_ingest.py --dir data/paper/cprime \
      --r1 data/paper/cprime/labels_r1.csv --r2 data/paper/cprime/labels_r2.csv \
      --r3 data/paper/cprime/labels_r3.csv [--structural data/paper/cprime/labels_structural.csv]
Writes results.json and results.md into --dir and prints the go/no-go line."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.paper.cprime_agreement import (  # noqa: E402
    ht_rate,
    krippendorff_alpha_nominal,
    per_class_agreement,
    raw_agreement,
    wilson,
)
from scripts.paper.cprime_strata import STRATA  # noqa: E402

CODES = ("ok", "multiple", "sub_part", "not_the_organism", "indeterminate")
NOVEL = ("novel_multiple", "novel_not_the_organism", "novel_sub_part")
GO_PPV = 0.8
GO_FN = 0.1


def read_labels(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            label = (row.get("label") or "").strip().lower()
            if label not in CODES:
                raise ValueError(
                    f"{path}:{i} anon_id={row.get('anon_id')} has label {label!r}; allowed {CODES}"
                )
            out[row["anon_id"]] = label
    return out


def adjudicate(r1: dict, r2: dict, r3: dict | None) -> dict[str, dict]:
    r3 = r3 or {}
    final: dict[str, dict] = {}
    for a in sorted(set(r1) | set(r2)):
        l1, l2 = r1.get(a), r2.get(a)
        if l1 is not None and l1 == l2:
            final[a] = {"label": l1, "source": "agree"}
        elif a in r3:
            final[a] = {"label": r3[a], "source": "adjudicated"}
        else:
            final[a] = {"label": "unresolved", "source": "unresolved"}
    return final


def human_inadmissible(label: str) -> bool | None:
    if label == "ok":
        return False
    if label in ("multiple", "sub_part", "not_the_organism"):
        return True
    return None


def estimate(
    manifest_rows: list[dict], final: dict[str, dict], r1: dict, r2: dict, r3: dict | None = None
) -> dict:
    main = [m for m in manifest_rows if m["set"] == "main"]
    calib = [m for m in manifest_rows if m["set"] == "calibration"]
    items = []
    n_unresolved = n_indet = 0
    for m in main:
        f = final.get(m["anon_id"], {"label": "unresolved"})
        if f["label"] == "unresolved":
            n_unresolved += 1
            continue
        hi = human_inadmissible(f["label"])
        if hi is None:
            n_indet += 1
            continue
        items.append(
            {
                **m,
                "inclusion_prob": float(m["inclusion_prob"]),
                "human_inadmissible": hi,
                "gate_rejected": m["stratum"] != "admitted",
            }
        )

    per: dict[str, dict] = {}
    pop_by = defaultdict(int)
    samp_by = defaultdict(int)
    for m in main:
        samp_by[m["stratum"]] += 1
        p = float(m["inclusion_prob"])
        pop_by[m["stratum"]] = max(pop_by[m["stratum"]], round(1 / p) if p > 0 else 0)
    for s in STRATA:
        lab = [i for i in items if i["stratum"] == s]
        k = sum(i["human_inadmissible"] for i in lab)
        n = len(lab)
        per[s] = {
            "population_est": pop_by[s] * samp_by[s] if s != "admitted" else None,
            "sampled": samp_by[s],
            "labelled": n,
            "inadmissible": k,
            "ppv": (k / n) if n else None,
            "ppv_ci": list(wilson(k, n)) if n else None,
        }

    novel = [i for i in items if i["stratum"] in NOVEL]
    nk, nn = sum(i["human_inadmissible"] for i in novel), len(novel)
    adm = [i for i in items if i["stratum"] == "admitted"]
    ak, an = sum(i["human_inadmissible"] for i in adm), len(adm)

    weighted = {
        "sensitivity": ht_rate(
            items,
            numerator=lambda i: i["gate_rejected"],
            denominator=lambda i: i["human_inadmissible"],
        ),
        "specificity": ht_rate(
            items,
            numerator=lambda i: not i["gate_rejected"],
            denominator=lambda i: not i["human_inadmissible"],
        ),
        "gate_reject_rate": ht_rate(
            items, numerator=lambda i: i["gate_rejected"], denominator=lambda i: True
        ),
        "human_inadmissible_rate": ht_rate(
            items, numerator=lambda i: i["human_inadmissible"], denominator=lambda i: True
        ),
    }

    ids = [m["anon_id"] for m in main if m["anon_id"] in r1 and m["anon_id"] in r2]
    a, b = [r1[i] for i in ids], [r2[i] for i in ids]
    units2 = [[r1.get(i), r2.get(i)] for i in ids]
    units3 = [[r1.get(i), r2.get(i), (r3 or {}).get(i)] for i in ids]
    agreement = {
        "raw": raw_agreement(a, b) if ids else None,
        "per_class": per_class_agreement(a, b) if ids else {},
        "alpha_r1_r2": krippendorff_alpha_nominal(units2),
        "alpha_all3": krippendorff_alpha_nominal(units3) if r3 else None,
    }
    res = {
        "n_main": len(main),
        "n_calibration": len(calib),
        "n_unresolved": n_unresolved,
        "n_indeterminate": n_indet,
        "per_stratum": per,
        "novel_ppv": {
            "k": nk,
            "n": nn,
            "ppv": (nk / nn) if nn else None,
            "ci": list(wilson(nk, nn)) if nn else None,
        },
        "admitted_fn": {
            "k": ak,
            "n": an,
            "rate": (ak / an) if an else None,
            "ci": list(wilson(ak, an)) if an else None,
        },
        "weighted": weighted,
        "agreement": agreement,
    }
    go, reason = go_no_go(res)
    res["go"], res["go_reason"] = go, reason
    return res


def go_no_go(res: dict) -> tuple[bool, str]:
    ppv = res["novel_ppv"].get("ppv")
    fn = res["admitted_fn"].get("rate")
    if res.get("n_unresolved", 0):
        return (
            False,
            f"{res['n_unresolved']} unresolved disagreement(s); adjudicate before deciding",
        )
    if ppv is None or fn is None:
        return False, "missing labels for the novel stratum or the admitted cell"
    ok = ppv >= GO_PPV and fn < GO_FN
    verdict = "GO" if ok else "NO-GO"
    return (
        ok,
        f"{verdict}: novel PPV {ppv:.3f} {'>=' if ppv >= GO_PPV else '<'} {GO_PPV} and admitted FN {fn:.3f} {'<' if fn < GO_FN else '>='} {GO_FN}",
    )


def render_markdown(res: dict) -> str:
    def f(x, d=3):
        return "—" if x is None else f"{x:.{d}f}"

    lines = [
        "# Study C′ results",
        "",
        f"Main items {res['n_main']}, calibration {res['n_calibration']} (excluded), "
        f"indeterminate {res['n_indeterminate']}, unresolved {res['n_unresolved']}.",
        "",
        "| stratum | sampled | labelled | inadmissible | PPV | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for s, d in res["per_stratum"].items():
        ci = "—" if not d["ppv_ci"] else f"{d['ppv_ci'][0]:.2f}–{d['ppv_ci'][1]:.2f}"
        lines.append(
            f"| {s} | {d['sampled']} | {d['labelled']} | {d['inadmissible']} | {f(d['ppv'])} | {ci} |"
        )
    w, ag = res["weighted"], res["agreement"]
    lines += [
        "",
        f"Novel-stratum PPV {f(res['novel_ppv']['ppv'])} (k={res['novel_ppv']['k']}, n={res['novel_ppv']['n']}); "
        f"admitted false-negative rate {f(res['admitted_fn']['rate'])} (k={res['admitted_fn']['k']}, n={res['admitted_fn']['n']}).",
        f"Population-weighted sensitivity {f(w['sensitivity'])}, specificity {f(w['specificity'])}.",
        f"Raw agreement {f(ag['raw'])}; Krippendorff α (r1,r2) {f(ag['alpha_r1_r2'])}; α (all three) {f(ag['alpha_all3'])}.",
        "",
        f"**Go/no-go:** {res['go_reason']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimate Study C′ results from rater CSVs.")
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--r1", type=Path, required=True)
    ap.add_argument("--r2", type=Path, required=True)
    ap.add_argument("--r3", type=Path)
    ap.add_argument(
        "--structural",
        type=Path,
        help="3D-literate rater's labels for struct_* items; override r1/r2/r3 there",
    )
    args = ap.parse_args()
    manifest = list(csv.DictReader(open(args.dir / "manifest.csv", newline="")))
    r1, r2 = read_labels(args.r1), read_labels(args.r2)
    r3 = read_labels(args.r3) if args.r3 else None
    final = adjudicate(r1, r2, r3)
    if args.structural:
        for a, lab in read_labels(args.structural).items():
            final[a] = {"label": lab, "source": "structural_rater"}
    res = estimate(manifest, final, r1, r2, r3)
    (args.dir / "results.json").write_text(json.dumps(res, indent=2))
    (args.dir / "results.md").write_text(render_markdown(res))
    print(res["go_reason"])
    return 0 if res["go"] else 1


if __name__ == "__main__":
    sys.exit(main())
