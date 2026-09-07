# scripts/reproduce_board.py
"""Rebuild the Taxon3D leaderboard from the public `preferences.jsonl` alone.

The Hugging Face dataset (`musharna/taxon3d-corpus-v1`) ships every vote the leaderboard counts,
by metadata. This script fits the same Bradley-Terry model the site fits (`app/ranking.py`), from
that file and nothing else, so anyone can check the published board without our database.

Same rules as the site (`app/service.py::_matches_for_scope`): a `tie` is one win each way, a
`bad` vote is dropped, and the bootstrap resamples whole voters (`--cluster voter`, the site's
default) or single ballots. Boards are per paradigm on the site; this fit is over every
generator at once, which is the same fit — paradigms never meet, so their components are only
coupled through the geometric-mean normalisation, and the within-paradigm order is identical.

Usage:
  .venv/bin/python scripts/reproduce_board.py preferences.jsonl                 # local file
  .venv/bin/python scripts/reproduce_board.py --hub                             # fetch from the Hub
  .venv/bin/python scripts/reproduce_board.py --hub --criterion botanical_plausibility
  .venv/bin/python scripts/reproduce_board.py --hub --compare https://taxon3d.org/api/leaderboard
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ranking  # noqa: E402

HUB_URL = (
    "https://huggingface.co/datasets/musharna/taxon3d-corpus-v1/resolve/main/preferences.jsonl"
)
# Cloudflare fronts taxon3d.org and 403s the default urllib agent; name ourselves honestly.
UA = {"User-Agent": "taxon3d-reproduce-board/1.0 (+https://github.com/musharna/taxon3d)"}


def load_preferences(path: Path) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def fetch_hub(dest: Path) -> Path:
    req = urllib.request.Request(HUB_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def rebuild(
    prefs: list[dict], *, criterion: str, bootstrap: int = 200, cluster: str = "voter"
) -> list[dict]:
    """Rows sorted by BT score, one per generator with at least one decisive game."""
    slugs = sorted({r["a_generator_slug"] for r in prefs} | {r["b_generator_slug"] for r in prefs})
    idx = {s: i for i, s in enumerate(slugs)}
    matches: list[tuple[int, int]] = []
    groups: list[int] = []
    gkeys: dict[str, int] = {}
    for n, r in enumerate(prefs):
        if r["criterion"] != criterion:
            continue
        a, b = idx[r["a_generator_slug"]], idx[r["b_generator_slug"]]
        key = r["voter"] if cluster == "voter" else str(n)
        g = gkeys.setdefault(key, len(gkeys))
        if r["winner"] == "a":
            matches.append((a, b))
            groups.append(g)
        elif r["winner"] == "b":
            matches.append((b, a))
            groups.append(g)
        elif r["winner"] == "tie":
            matches.extend([(a, b), (b, a)])
            groups.extend([g, g])
    res = ranking.bradley_terry(
        list(range(len(slugs))), matches, bootstrap=bootstrap, groups=groups
    )
    rows = [
        {
            "slug": s,
            "bt_score": round(res.scores.get(i, 0.0), 1),
            "bt_lower": round(res.lower.get(i, 0.0), 1),
            "bt_upper": round(res.upper.get(i, 0.0), 1),
            "n_games": int(res.n_games.get(i, 0)),
        }
        for s, i in idx.items()
        if res.n_games.get(i, 0) > 0
    ]
    rows.sort(key=lambda r: (-r["bt_score"], r["slug"]))
    return rows


def compare(rows: list[dict], api_url: str) -> dict:
    """Per-slug score deltas against a live `/api/leaderboard` response, and the count of slugs
    whose live rank neighbours agree with ours. Boards differ only by bootstrap noise and by
    votes cast since the dataset was published; both show up here as small deltas."""
    with urllib.request.urlopen(urllib.request.Request(api_url, headers=UA), timeout=60) as r:
        live = json.load(r)
    live_by = {row["slug"]: row for row in live.get("rows", [])}
    deltas = {
        r["slug"]: round(r["bt_score"] - live_by[r["slug"]]["bt_score"], 1)
        for r in rows
        if r["slug"] in live_by
    }
    return {
        "compared": len(deltas),
        "only_here": sorted(r["slug"] for r in rows if r["slug"] not in live_by),
        "only_live": sorted(set(live_by) - {r["slug"] for r in rows}),
        "max_abs_delta": max((abs(d) for d in deltas.values()), default=0.0),
        "deltas": deltas,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("path", nargs="?", type=Path, help="preferences.jsonl (omit with --hub)")
    ap.add_argument("--hub", action="store_true", help="download preferences.jsonl from the Hub")
    ap.add_argument("--criterion", default="overall")
    ap.add_argument("--bootstrap", type=int, default=200)
    ap.add_argument("--cluster", choices=["voter", "ballot"], default="voter")
    ap.add_argument("--compare", metavar="URL", help="a /api/leaderboard URL to diff against")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()
    if args.hub:
        args.path = fetch_hub(Path("preferences.hub.jsonl"))
    if args.path is None:
        ap.error("give a preferences.jsonl path or --hub")
    prefs = load_preferences(args.path)
    rows = rebuild(prefs, criterion=args.criterion, bootstrap=args.bootstrap, cluster=args.cluster)
    out: dict = {"criterion": args.criterion, "n_preferences": len(prefs), "rows": rows}
    if args.compare:
        out["compare"] = compare(rows, args.compare)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"criterion={args.criterion}  preferences={len(prefs)}  cluster={args.cluster}")
    print(f"{'#':>3} {'generator':40} {'BT':>7} {'lo':>7} {'hi':>7} {'games':>5}")
    for i, r in enumerate(rows, 1):
        print(
            f"{i:>3} {r['slug']:40} {r['bt_score']:7.1f} {r['bt_lower']:7.1f} {r['bt_upper']:7.1f} {r['n_games']:5d}"
        )
    if args.compare:
        c = out["compare"]
        print(
            f"\ncompared {c['compared']} generators with {args.compare}; max |delta| = {c['max_abs_delta']}"
        )
        if c["only_here"] or c["only_live"]:
            print(f"only here: {c['only_here']}\nonly live: {c['only_live']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
