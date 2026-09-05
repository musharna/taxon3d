"""Export the redistribute-cleared Taxon3D corpus as a Hugging Face dataset.

Separate from `export_public.py` because the OUTPUT SHAPE differs: flat `meshes/<id>.glb` plus
JSONL tables, versus the site bundle's `rows.json` + `assets/<original path>` + `gt/` + LODs +
manifest. Both share the licence and admissibility gate by IMPORTING it — a second copy of that
predicate is how a mesh we have no right to ship would eventually ship.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import admissibility  # noqa: E402
from app import config  # noqa: E402
from app import public_export  # noqa: E402
from app import service  # noqa: E402
from app.dataset import voter_pseudonym  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.kingdoms import KINGDOM_OF  # noqa: E402
from app.models import (  # noqa: E402
    Admissibility,
    Category,
    Comparison,
    Completeness,
    Criterion,
    Generator,
    JudgeRating,
    ModelOutput,
    Task,
    Vote,
    VoterSession,
)
from app.public_export import IncludeSet  # noqa: E402
from app.reference_provenance import assert_recon_photos_cleared  # noqa: E402


#: Histogram key for outputs we generated ourselves, which carry `license=None`.
#:
#: `str(None)` would write `"None"` into a manifest that ships inside the uploaded tree, where it
#: reads as a row whose licence nobody bothered to record — the opposite of the truth. A null
#: licence here means no third-party licence EXISTS, because we made the mesh, so the collection's
#: CC-BY-4.0 is the whole story.
#:
#: Safe to state that flatly only because the gate runs first: `normalize_license(None)` is None
#: (app/licensing.py), None is not in `REDISTRIBUTABLE_LICENSES`, so `check_licenses` raises on
#: any NON-own output with a null licence rather than letting it reach this histogram.
OWN_OUTPUT_LICENSE_KEY = "own-output (no third-party licence)"


@dataclass
class ExportAccounting:
    """Why each candidate output did or did not ship, counted at the gate that decided it.

    The card publishes these numbers, so they are measured at the moment of the drop rather than
    re-derived afterwards: a second implementation of "which outputs are commercial" would be free
    to disagree with the filter that actually ran, and the card would report the disagreement as
    fact. Every field below is a difference in set size across one gate in `resolve_hf_include`.

    `candidates == shipped + withdrawn + gold + restricted_license + not_admitted` is an invariant,
    not a coincidence: the gates partition the pool, and an unaccounted-for drop is a bug worth
    failing on rather than papering over with an "other" bucket.
    """

    candidates: int = 0
    shipped: int = 0
    withdrawn: int = 0
    gold: int = 0
    restricted_license: int = 0
    not_admitted: int = 0

    def check(self) -> None:
        """Raise if the reasons do not sum to the pool.

        Fail loud: a card that under-counts its own exclusions is precisely the "distributes bytes
        while describing something else" failure this export exists to prevent.
        """
        total = (
            self.shipped + self.withdrawn + self.gold + self.restricted_license + self.not_admitted
        )
        if total != self.candidates:
            raise RuntimeError(
                f"export accounting does not reconcile: {self.candidates} candidates but "
                f"{total} accounted for ({self!r}). Refusing to publish counts that do not add up."
            )


def _iso(value):
    return value.isoformat() if value is not None else None


def _drop_hidden(db: Session, inc: IncludeSet) -> None:
    """Remove every output whose MESH was withdrawn, at EVERY posture, in place.

    Keyed on `asset_path`, not on `id`: an output is dropped if ANY row pointing at its mesh is
    hidden, including a row that is not itself shipping. See the asset-vs-row note at the bottom
    of this docstring for why the row key was wrong.

    THE SITE ENFORCES HIDDEN-NESS AT SERVE TIME; A DOWNLOADABLE DATASET HAS NO SERVE TIME.
    `/media/o/{id}` (app/main.py) 404s a hidden output on every request, so on the live arena a
    withdrawal takes effect retroactively. Once a mesh is inside a published tarball there is no
    request to intercept and no way to take it back — so the withdrawal has to happen HERE, before
    the bytes are copied.

    None of the shared gates does this: `resolve_include_ids`, `filter_include_for_posture`,
    `check_licenses`, and the admissibility rubric all ignore `hidden_at` entirely (verified
    2026-08-20: `grep -c hidden_at` -> 0 in app/public_export.py, app/admissibility.py,
    app/reference_provenance.py). This is not an oversight in those modules — the site bundle they
    serve re-checks `hidden_at` per request — it is a gap that only a flat export exposes.

    Outputs land here for two unrelated reasons and both must stay out: automatic withdrawal on
    accumulated voter flags (app/flags.py) and manual withdrawal for a rights reason
    (scripts/disposition_rose_soybean.py). Measured against data/study/arena-study.db on
    2026-08-20: 127 hidden outputs, of which 23 were otherwise redistribute-clear and would have
    shipped.

    Applied to `gold_output_ids` too. That set is emptied immediately afterwards, but a filter
    that depends on a later line for its correctness is a filter waiting to be reordered.

    WHY THE ASSET AND NOT THE ROW. Two `ModelOutput` rows may name one `asset_path` — by design
    for gold decoys (`public_export.effective_provenance` exists for exactly that aliasing), and
    in practice outside that case too. Measured on data/study/arena-study.db 2026-08-20: two
    hidden rows alias a visible one, and 322/100 is not a gold pair. Keying on `id` is right for
    the live site, where `/media/o/322` 404s while `/media/o/100` keeps serving the same bytes —
    correct, because hiding is a per-publication act and an un-hide restores it. `copy_meshes`
    copies `root / o.asset_path`, so a row-keyed export writes those withdrawn bytes as
    `meshes/100.glb`, and a published tarball has no un-publish.

    The reason we cannot be more precise is that `hidden_at` records no reason: it is a bare
    nullable timestamp (app/models.py) written both by voter-flag withdrawal (app/flags.py) and
    by licensing withdrawal (scripts/disposition_rose_soybean.py). Nothing in the schema separates
    "this render is bad" from "we may not distribute these bytes". Under that ambiguity the costs
    are asymmetric — over-filtering loses one row of corpus, under-filtering is an unretractable
    distribution — so the conservative reading wins. If a `hidden_reason` column is ever added,
    this is the call site that should narrow to rights-based withdrawals only.
    """
    hidden_assets = set(
        db.execute(
            select(ModelOutput.asset_path).where(ModelOutput.hidden_at.is_not(None))
        ).scalars()
    )
    if not hidden_assets:
        return
    # `asset_path` is non-nullable (app/models.py), so no NULL can enter this set and silently
    # never match — `IN` would drop a NULL comparison rather than raise.
    withdrawn = set(
        db.execute(
            select(ModelOutput.id).where(ModelOutput.asset_path.in_(hidden_assets))
        ).scalars()
    )
    inc.output_ids -= withdrawn
    inc.gold_output_ids -= withdrawn


def resolve_hf_include(
    db: Session,
    *,
    task_titles: list[str],
    generator_slugs: list[str],
    posture: str = "redistribute",
    accounting: ExportAccounting | None = None,
) -> IncludeSet:
    """Run the full export gate chain and return the cleared include set.

    Pass an `ExportAccounting` to have each gate record how many outputs it dropped and why; the
    dataset card publishes those counts. It is an out-parameter rather than a second return value
    so existing callers and tests keep working unchanged.

    `posture` exists so the test suite can run this identical path at "display" and assert it
    yields strictly more outputs. Without that control, a filter that never ran is
    indistinguishable from one that ran perfectly, because the seed corpus contains no
    commercial-source outputs.

    Gold outputs are emptied FIRST, not last: `ModelOutput.is_gold` marks attention-check decoys
    and `Comparison.gold_expected` records the answer, so publishing either lets anyone pass the
    check and collapses `trust = (gold_passed + 1) / (gold_seen + 1)`. Dropping them up front
    means no downstream gate has to be trusted to leave them alone, and no downstream gate can
    abort the export over an output that was never a candidate — which is why neither
    `filter_gold_for_posture` nor `assert_recon_photos_cleared_for_gold` is called here. Their
    only role would be narrowing a set that is already empty; a gold row aliases a non-gold twin's
    asset (public_export.effective_provenance) and that twin is checked on its own id below.
    """
    acct = accounting if accounting is not None else ExportAccounting()

    inc = public_export.resolve_include_ids(
        db, task_titles=task_titles, generator_slugs=generator_slugs
    )
    # The candidate pool is measured HERE, before any HF gate narrows it, because this is the only
    # point at which "what we could have shipped" is still knowable. Gold ids are counted into it
    # explicitly: they are candidates that a gate removes, and folding them in silently would make
    # the reasons fail to sum to the pool.
    acct.candidates = len(inc.output_ids | inc.gold_output_ids)

    # Measured across BOTH sets, because `_drop_hidden` withdraws from the gold set as well. Count
    # only `output_ids` here and a withdrawn GOLD output lands in no bucket at all: it is gone
    # before the gold purge can count it, and `check()` then fails a legitimate export.
    before_hidden = len(inc.output_ids | inc.gold_output_ids)
    _drop_hidden(db, inc)
    acct.withdrawn = before_hidden - len(inc.output_ids | inc.gold_output_ids)

    acct.gold = len(inc.gold_output_ids)
    inc.gold_output_ids = set()

    # Refuse BEFORE the admissibility filter runs: that gate treats "never evaluated" as "not
    # admitted", so an unscored output would otherwise vanish silently and the export would report
    # success on a short corpus. Ask the question only of outputs this posture would actually
    # ship, though — a missing verdict on a commercial-API output that the redistribute licence
    # filter drops anyway is not a reason to abort a legitimate export. `eligible` is exactly that
    # set: the real posture predicate evaluated with an EMPTY gated set, so licence/source/hard-
    # exclude rules apply and admissibility does not.
    eligible = IncludeSet(
        generator_ids=set(inc.generator_ids),
        task_ids=set(inc.task_ids),
        output_ids=set(inc.output_ids),
    )
    before_license = len(inc.output_ids)
    public_export.filter_include_for_posture(db, eligible, posture, set())
    admissibility.assert_rubric_coverage(db, eligible.output_ids)
    # `eligible` is the pool with licence/source rules applied and admissibility NOT applied, so
    # the two drop reasons separate cleanly here. Reporting the combined drop as "commercial
    # terms" would attribute admissibility failures to licensing — a claim about other people's
    # terms of service that we would have no basis for.
    acct.restricted_license = before_license - len(eligible.output_ids)

    gated = admissibility.non_admitted_output_ids(db)
    public_export.filter_include_for_posture(db, inc, posture, gated)
    acct.not_admitted = len(eligible.output_ids) - len(inc.output_ids)
    acct.shipped = len(inc.output_ids)
    acct.check()

    if posture == "redistribute":
        public_export.check_licenses(db, inc.output_ids)
        assert_recon_photos_cleared(db, inc.output_ids)
    return inc


def _criterion_slug(db: Session, criterion_id: int | None) -> str:
    """Resolve a criterion id to its slug, or raise.

    ONE None policy for both call sites below. They used to disagree three lines apart — the vote
    table coerced a dangling reference to `None` while the judge table dereferenced the same kind
    of lookup unguarded — so the same broken row produced a silent null in one table and an
    AttributeError in the other. Fail loud is the right half of that pair: `Comparison.criterion_id`
    and `JudgeRating.criterion_id` are both non-nullable (app/models.py), so a lookup that returns
    nothing means the criterion row is gone, and "A beat B on ???" is not data a reader can use.
    """
    crit = db.get(Criterion, criterion_id) if criterion_id is not None else None
    if crit is None:
        raise RuntimeError(
            f"criterion {criterion_id!r} does not resolve — refusing to emit a row whose"
            " comparison axis is unknown"
        )
    return crit.slug


def _category_slug(db: Session, category_id: int | None) -> str | None:
    """Resolve an optional category id to its slug. Unlike criterion, NULL is meaningful here:
    `JudgeRating.category_id` is nullable and NULL means the all-kingdoms board."""
    if category_id is None:
        return None
    cat = db.get(Category, category_id)
    if cat is None:
        raise RuntimeError(f"category {category_id!r} does not resolve — refusing to emit the row")
    return cat.slug


def build_tables(db: Session, inc: IncludeSet) -> dict[str, list[dict]]:
    """Build the six JSONL tables for the cleared include set.

    Every table is keyed on `output_id`, and every row emitted here must reference an output that
    is actually shipping — a verdict pointing at a mesh nobody can see is worse than no verdict.
    """
    oids = sorted(inc.output_ids)
    oid_set = set(oids)

    outputs: list[dict] = []
    for oid in oids:
        o = db.get(ModelOutput, oid)
        task = db.get(Task, o.task_id)
        gen = db.get(Generator, o.generator_id)
        cat = db.get(Category, task.category_id)
        outputs.append(
            {
                "output_id": o.id,
                "task_title": task.title,
                "kingdom": KINGDOM_OF.get(cat.slug, "unknown"),
                "paradigm": gen.paradigm,
                "generator_slug": gen.slug,
                "source": o.source,
                "license": o.license,
                "attribution": o.attribution,
                "external_url": o.external_url,
                "created": _iso(o.created),
                "mesh_path": f"meshes/{o.id}.glb",
            }
        )

    adm = [
        {
            "output_id": a.output_id,
            "predicate": a.predicate,
            "admit": a.admit,
            "reason": a.reason,
            "detail_json": a.detail_json,
            "version": a.version,
            "computed": _iso(a.computed),
        }
        for a in db.execute(
            select(Admissibility).where(Admissibility.output_id.in_(oids))
        ).scalars()
    ]

    comp = [
        {
            "output_id": c.output_id,
            "category": c.category,
            "score": c.score,
            "checklist_json": c.checklist_json,
            "judge_model": c.judge_model,
            "scorer_version": c.scorer_version,
            "computed": _iso(c.computed),
        }
        for c in db.execute(select(Completeness).where(Completeness.output_id.in_(oids))).scalars()
    ]

    # Resolved votes only, gold comparisons excluded, and BOTH sides must be shipping — otherwise
    # a row would point at a mesh the reader cannot inspect.
    votes: list[dict] = []
    rows = db.execute(
        select(Comparison, Vote)
        .join(Vote, Vote.comparison_id == Comparison.id)
        .where(Comparison.is_gold.is_(False))
    ).all()
    for c, v in rows:
        if c.output_a_id not in oid_set or c.output_b_id not in oid_set:
            continue
        votes.append(
            {
                "output_a_id": c.output_a_id,
                "output_b_id": c.output_b_id,
                "winner": v.winner,
                "criterion": _criterion_slug(db, c.criterion_id),
                "created": _iso(v.created),
            }
        )

    # Every vote the LEADERBOARD counts, shipped by metadata. `votes` above requires both meshes
    # to ship, and the arena's human votes are cast almost entirely on commercial-API outputs whose
    # meshes never will (measured 2026-09-05: 1472 of 1609 prod votes had neither side in the
    # redistribute set), so that table froze at the July own-vs-own votes. The preference labels
    # are ours; only the meshes are the providers'. A withheld side is named by id, generator and
    # licence and flagged `*_mesh_available=False` — no path into `meshes/` is written for it.
    #
    # The predicates are the board's own (`service._published_vote_filters`), not a copy: gold
    # decoys, low-trust voters and excluded cohorts drop here for exactly the reasons they drop
    # from the published ranking, and a fourth predicate added there reaches this table too.
    # `voter` is `dataset.voter_pseudonym`, the same derivation the site's export uses, so the two
    # can be joined; the raw session id is the voter's credential and never leaves the server.
    side_meta: dict[int, tuple[str, str | None]] = {}

    def _side(oid: int) -> tuple[str, str | None]:
        if oid not in side_meta:
            o = db.get(ModelOutput, oid)
            side_meta[oid] = (db.get(Generator, o.generator_id).slug, o.license)
        return side_meta[oid]

    preferences: list[dict] = []
    pref_rows = db.execute(
        select(Comparison, Vote, VoterSession)
        .join(Vote, Vote.comparison_id == Comparison.id)
        .outerjoin(VoterSession, VoterSession.session_id == Vote.session_id)
        .where(*service._published_vote_filters())
        .order_by(Vote.id)
    ).all()
    for c, v, vs in pref_rows:
        a_slug, a_lic = _side(c.output_a_id)
        b_slug, b_lic = _side(c.output_b_id)
        preferences.append(
            {
                "output_a_id": c.output_a_id,
                "output_b_id": c.output_b_id,
                "winner": v.winner,
                "criterion": _criterion_slug(db, c.criterion_id),
                "task_title": db.get(Task, c.task_id).title,
                "a_generator_slug": a_slug,
                "b_generator_slug": b_slug,
                "a_license": a_lic,
                "b_license": b_lic,
                "a_mesh_available": c.output_a_id in oid_set,
                "b_mesh_available": c.output_b_id in oid_set,
                "voter": voter_pseudonym(v.session_id),
                "cohort": vs.cohort if vs is not None else None,
                "created": _iso(v.created),
            }
        )

    # Only generators that actually have a mesh in this dataset. Written as a plain set
    # comprehension rather than a truthiness `and` — a generator id of 0 would silently drop out
    # of an `and`-based filter.
    gen_ids = {db.get(ModelOutput, r["output_id"]).generator_id for r in outputs}
    judge = [
        {
            "generator_slug": db.get(Generator, j.generator_id).slug,
            # `category` completes the row's identity. `uq_judge_rating_scope` is
            # (generator_id, category_id, criterion_id, view_condition) — omitting the category
            # was harmless only because every row is NULL today (the all-kingdoms board). The
            # moment per-kingdom judge boards populate, an export without it emits several rows
            # sharing a key and disagreeing about bt_score, with nothing in the file saying why.
            # Emitted as a slug rather than the raw id: no category table ships here, so an id
            # would be unjoinable. None = the all-kingdoms board.
            "category": _category_slug(db, j.category_id),
            "criterion": _criterion_slug(db, j.criterion_id),
            "view_condition": j.view_condition,
            "bt_score": j.bt_score,
            "bt_lower": j.bt_lower,
            "bt_upper": j.bt_upper,
            "n_games": j.n_games,
            "judge_model": j.judge_model,
            "updated": _iso(j.updated),
        }
        for j in db.execute(
            select(JudgeRating).where(JudgeRating.generator_id.in_(gen_ids))
        ).scalars()
    ]

    return {
        "outputs": outputs,
        "admissibility": adm,
        "completeness": comp,
        "votes": votes,
        "preferences": preferences,
        "judge_ratings": judge,
    }


def _asset_root() -> Path:
    """Indirection so a test can point the root somewhere empty and prove we fail loud."""
    return Path(config.ASSET_DIR)


_CARD = """---
license: cc-by-4.0
task_categories:
  - image-to-3d
  - text-to-3d
tags:
  - 3d
  - biology
  - organisms
  - human-preference
  - evaluation
{configs}
---

# Taxon3D — an admissibility-gated organism corpus

Generated 3D models of organisms, each carrying a **reference-free judgement of whether it is
biologically admissible** — not just whether people liked it.

Most 3D generation benchmarks rank outputs by human preference alone. Taxon3D runs every candidate
through a pre-vote admissibility gate first: an output is admitted only if it passes *every*
predicate in the active rubric (structural integrity and organism completeness always; semantic
identity too, when the semantic predicate is running in gate mode, which it is in production
today — see `app/admissibility.py`'s `DEFAULT_RUBRIC` and `_effective_rubric`). The
`admissibility` table is that judgement, and it is the point of this dataset.

Live arena: https://taxon3d.org

## Contents

| file | rows | what it is |
| --- | --- | --- |
| `meshes/<output_id>.glb` | {n_meshes} | Original, uncompressed meshes |
| `admissibility.jsonl` | {n_admissibility} | **The headline.** One row per (output, predicate) |
| `completeness.jsonl` | {n_completeness} | Per-organ checklist behind the completeness predicate |
| `outputs.jsonl` | {n_outputs} | Taxon, kingdom, paradigm, generator, licence, attribution |
| `votes.jsonl` | {n_votes} | Resolved human pairwise comparisons where BOTH meshes ship |
| `preferences.jsonl` | {n_preferences} | **Every vote the leaderboard counts**, by metadata; withheld sides flagged via `a_mesh_available` / `b_mesh_available` |
| `judge_ratings.jsonl` | {n_judge_ratings} | VLM-judge Bradley-Terry ratings per generator |

## What is NOT here, and why

{withheld_summary}

- **Commercial-API outputs.** Much of the live arena is generated by commercial services whose
  terms permit display but not redistribution, so those outputs are excluded here. **This corpus is
  therefore not the whole arena**, and per-generator counts are not a sample of it. Human votes
  are cast mostly on those withheld outputs, so `votes.jsonl` (both meshes ship) is a small
  own-vs-own subset. `preferences.jsonl` carries every vote the leaderboard counts instead,
  naming a withheld side by output id, generator and licence only — no mesh, no path.
  `a_mesh_available` / `b_mesh_available` say whether that side's mesh is in `meshes/`. The
  preference labels are ours and ship under the collection licence; the withheld meshes stay
  with their providers. `voter` is a stable pseudonym (sha256 of the session credential,
  truncated), never the credential itself, and `cohort` tags recruited waves.
- **Reference / input photos.** Recon outputs are reconstructed from photographs. The photos are
  not redistributed here, so recon rows are not end-to-end reproducible from this dataset alone.
- **Attention-check assets.** The arena uses gold decoy pairs to detect inattentive voters.
  Publishing them would destroy the mechanism, so they and their comparisons are excluded.

## Licence

`CC-BY-4.0`. Per-item attribution is in `outputs.jsonl` (`license`, `attribution`,
`external_url`) — honour the per-item terms, which may be narrower than the collection licence.

## Meshes are originals, not what voters saw

Every admissibility verdict was computed by rendering the **original** mesh, so the originals ship.
The live site serves a compressed derivative. See `TRANSFORM.md` to reproduce exactly what voters
saw. This matters: presentation affects preference, so the mesh a judgement refers to is part of
the judgement.
"""

_TRANSFORM = """# Reproducing the voter-facing meshes

`meshes/` holds ORIGINALS. The meshes voters actually saw were derived at export time by two
deterministic steps, in this order:

1. **Texture downscale** — `app/texture_downscale.py`, PIL Lanczos resize re-encoded as WebP
   quality 97 at a 1536px long-edge cap. Two separate measurements are documented there, not one
   head-to-head, and this note keeps them separate on purpose:
   - Size-matched (~0.52-0.53 MB) against `gltf-transform resize` re-encoding the same source:
     28.5 dB PSNR for `gltf-transform resize` vs **42.2 dB** for PIL Lanczos + WebP at quality 92
     — this is the comparison that ruled `gltf-transform resize` out.
   - The shipped config (1536px, quality 97) was chosen from a separate sweep with no
     `gltf-transform` comparison at that setting: **43.5 dB**, PSNR computed only over visible
     (alpha > 250) pixels at the viewer's render size.
2. **Geometry compression** — `app/mesh_compress.py`, Draco via `gltf-transform`. Kept only when
   it actually shrinks the file; when Draco enlarges a mesh the original ships unchanged.

Both live in the public repository: https://github.com/musharna/taxon3d

The `votes.jsonl` and `preferences.jsonl` rows refer to the derived meshes. The
`admissibility.jsonl` and `completeness.jsonl` rows refer to the originals in `meshes/`.
"""


def _withheld_summary(acct: ExportAccounting | None) -> str:
    """Render the accounting as the card's lead sentence on scale.

    Written as prose with the numbers inline rather than a table, because the number that matters
    is the RATIO — a reader who sees only "292 outputs" has no way to tell whether that is most of
    the arena or half of it. Each reason is named only when it actually removed something, so the
    card never claims an exclusion it did not make.
    """
    if acct is None:
        return ""
    reasons = [
        (
            acct.restricted_license,
            "under commercial-API terms that permit display but not redistribution",
        ),
        (acct.withdrawn, "withdrawn from publication"),
        (acct.gold, "attention-check decoys"),
        (acct.not_admitted, "not admitted by the rubric"),
    ]
    named = [f"{n} {label}" for n, label in reasons if n]
    if not named:
        return f"**All {acct.candidates} candidate outputs ship.** Nothing was withheld."
    withheld = acct.candidates - acct.shipped
    return (
        f"**{acct.shipped} of {acct.candidates} candidate outputs ship here; {withheld} are "
        f"withheld** — " + "; ".join(named) + "."
    )


#: The table the dataset viewer opens on. `admissibility` because the card calls it the headline,
#: and the default config is the first thing a visitor sees — so it should be the reason to look
#: rather than whichever table happens to sort first.
_DEFAULT_CONFIG = "admissibility"


def _viewer_configs(tables: dict[str, list[dict]]) -> str:
    """Render the card's `configs` YAML block from the tables actually written.

    Without this block the Hub gives the dataset NO viewer: the five tables are served as bare
    downloads, so the corpus is only inspectable by someone who has already decided to fetch 400 MB.
    For a dataset published to be found, that is the whole game.

    Derived from `tables`, never hand-listed, because `export_hf` writes `out / f"{name}.jsonl"`
    from these same keys. A hardcoded block would be a second enumeration of the tables, free to
    drift the moment a sixth is added — and the failure is silent in the worst way: a shipped table
    with no viewer, no error, and nothing on the page to suggest it is missing.
    """
    lines = ["configs:"]
    for name in tables:
        lines.append(f"  - config_name: {name}")
        lines.append(f'    data_files: "{name}.jsonl"')
        if name == _DEFAULT_CONFIG:
            lines.append("    default: true")
    return "\n".join(lines)


def write_cards(
    out_dir: Path,
    tables: dict[str, list[dict]],
    n_meshes: int,
    accounting: ExportAccounting | None = None,
) -> None:
    """Write the public-facing README.md dataset card and TRANSFORM.md into out_dir.

    Counts are read from `tables`/`n_meshes`/`accounting`, never hardcoded — a stale hardcoded
    count would be a lie that survives every future export.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(
        _CARD.format(
            configs=_viewer_configs(tables),
            n_meshes=n_meshes,
            n_outputs=len(tables["outputs"]),
            n_admissibility=len(tables["admissibility"]),
            n_completeness=len(tables["completeness"]),
            n_votes=len(tables["votes"]),
            n_preferences=len(tables["preferences"]),
            n_judge_ratings=len(tables["judge_ratings"]),
            withheld_summary=_withheld_summary(accounting),
        ),
        encoding="utf-8",
    )
    (out / "TRANSFORM.md").write_text(_TRANSFORM, encoding="utf-8")


def copy_meshes(db: Session, inc: IncludeSet, out_dir: Path) -> int:
    """Copy each cleared output's ORIGINAL mesh to out_dir/meshes/<output_id>.glb.

    No Draco, no texture downscale, no LOD. The site bundle applies those to a COPY at export
    time; the admissibility verdicts describe the original, so the original is what ships.
    Renaming to <output_id>.glb also drops the descriptive asset keys (e.g.
    `commissioned/openrouter-anthropic-claude-opus-4-8_11.glb`) and gives the tables a join key.
    """
    meshes = Path(out_dir) / "meshes"
    meshes.mkdir(parents=True, exist_ok=True)
    root = _asset_root()
    written = 0
    for oid in sorted(inc.output_ids):
        o = db.get(ModelOutput, oid)
        src = root / o.asset_path
        if not src.exists():
            raise FileNotFoundError(f"output {oid}: asset missing at {src}")
        shutil.copyfile(src, meshes / f"{oid}.glb")
        written += 1
    return written


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def export_hf(
    db: Session,
    *,
    task_titles: list[str],
    generator_slugs: list[str],
    out_dir,
    dry_run: bool = False,
) -> dict:
    """Run the gate chain, build the tables, and write the HF dataset tree to `out_dir`.

    `dry_run=True` runs every gate (so licence/coverage failures still raise) but writes nothing
    to disk — useful for the pre-publish preflight described in the plan.
    """
    acct = ExportAccounting()
    inc = resolve_hf_include(
        db, task_titles=task_titles, generator_slugs=generator_slugs, accounting=acct
    )
    if not inc.output_ids:
        raise RuntimeError("include set is empty — refusing to write an empty dataset")
    tables = build_tables(db, inc)
    # A per-item licence histogram, because the card's blanket `license: cc-by-4.0` is a claim
    # about the collection and NOT about every row in it: REDISTRIBUTABLE_LICENSES also admits
    # CC-BY-SA-3.0/4.0 and ODbL-1.0, whose share-alike terms are not "narrower than CC-BY" the way
    # the card's honour-the-per-item-terms note implies. A publisher has to be able to see the
    # real mix BEFORE upload, not reconstruct it from outputs.jsonl afterwards. `export_public.py`
    # has always built this; this exporter dropped it.
    licenses: dict[str, int] = {}
    for row in tables["outputs"]:
        key = OWN_OUTPUT_LICENSE_KEY if row["license"] is None else row["license"]
        licenses[key] = licenses.get(key, 0) + 1
    manifest = {
        "version": 1,
        "posture": "redistribute",
        "counts": {k: len(v) for k, v in tables.items()},
        "licenses": licenses,
        # The publisher's own view of the same numbers the card states, so a pre-upload check can
        # compare them against the live DB without re-parsing prose.
        "accounting": {
            "candidates": acct.candidates,
            "shipped": acct.shipped,
            "withdrawn": acct.withdrawn,
            "gold": acct.gold,
            "restricted_license": acct.restricted_license,
            "not_admitted": acct.not_admitted,
        },
    }
    if dry_run:
        manifest["dry_run"] = True
        return manifest

    out = Path(out_dir)
    # A path that exists but is not a directory has to be caught HERE. `any(out.iterdir())` below
    # raises NotADirectoryError from the stdlib before the intended message is ever built, so an
    # operator who typo'd `--out` gets a traceback that says nothing about what this script wanted
    # — on the one script whose entire job is refusing to publish the wrong bytes.
    if out.exists() and not out.is_dir():
        raise RuntimeError(
            f"refusing to export to {out} — it exists and is not a directory. Pass a --out that"
            " names a directory, or a path that does not exist yet."
        )
    # Refuse a non-empty target rather than writing into it. `meshes/<id>.glb` is keyed on output
    # id, so a stale file from an earlier run is NOT overwritten by this run — it survives beside
    # tables that no longer mention it and gets uploaded. The dangerous case is concrete: an
    # earlier `posture="display"` run through resolve_hf_include leaves commercial-API meshes on
    # disk, and a redistribute run that reuses the directory would publish them under a card that
    # says commercial outputs are excluded. Refusing beats clearing: deleting a directory the
    # caller named is not this script's decision to make.
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(
            f"refusing to export into non-empty directory {out} — a stale mesh from an earlier"
            " run would ship alongside tables that do not describe it. Remove it or pass a fresh"
            " --out."
        )
    out.mkdir(parents=True, exist_ok=True)
    n_meshes = copy_meshes(db, inc, out)
    for name, rows in tables.items():
        _write_jsonl(out / f"{name}.jsonl", rows)
    write_cards(out, tables, n_meshes=n_meshes, accounting=acct)
    manifest["counts"]["meshes"] = n_meshes
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the Taxon3D corpus as an HF dataset.")
    ap.add_argument("--tasks", required=True, help="comma-separated task titles")
    ap.add_argument("--generators", required=True, help="comma-separated generator slugs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        manifest = export_hf(
            db,
            task_titles=a.tasks.split(","),
            generator_slugs=a.generators.split(","),
            out_dir=a.out,
            dry_run=a.dry_run,
        )
    finally:
        db.close()
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
