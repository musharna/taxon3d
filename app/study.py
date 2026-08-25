"""Recruited-study support: cohort tagging and the completion gate.

The arena has never recorded an organic vote, which makes every explanation of that consistent
with the data — "nobody arrived" and "they arrived and bounced" are indistinguishable from a vote
count alone. A recruited cohort settles it, but only if its votes can be separated from ambient
traffic afterwards, and only if a participant can prove they finished. That is the whole scope
here.

**What is deliberately NOT collected: the recruitment platform's participant id.** Prolific
appends `PROLIFIC_PID` to the study URL, and storing it would create a re-identification surface
that buys nothing — a campaign LABEL is enough to attribute votes to the cohort, and
per-participant ballot counts already exist as `VoterSession.n_votes`. The id is ignored.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from . import config, integrity
from .models import VoterSession

#: Query parameter carrying the cohort label, e.g. `/arena?c=pilot-1`.
CAMPAIGN_PARAM = "c"

#: A label is stored in a DB column and echoed back in analysis output, so it is validated rather
#: than trusted: leading alphanumeric, then alphanumerics/dot/underscore/hyphen, 40 max — which is
#: the column width. Anything else is dropped, leaving the session UNTAGGED rather than storing
#: raw input under a name that later reads as a legitimate cohort.
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


def campaign_label(raw: str | None) -> str | None:
    """Return a validated cohort label, or None if absent/malformed."""
    value = (raw or "").strip()
    return value if _LABEL_RE.match(value) else None


def stamp_cohort(db: Session, session_id: str, label: str | None) -> None:
    """Record `label` as this voter's cohort. FIRST WRITE WINS; a no-op without a valid label.

    First-write-wins because the alternative silently rewrites history: a participant who wanders
    off and returns through a different link would be relabelled, and per-cohort vote counts would
    change meaning underneath a study that is still running. It also means an ambient visitor who
    later clicks a campaign link is counted honestly as ambient.

    Only called when a label is present, so ordinary traffic creates no `VoterSession` row it
    would not otherwise have created.
    """
    if label is None:
        return
    vs = integrity.get_or_create_session(db, session_id)
    if vs.cohort is None:
        vs.cohort = label
        db.commit()


def study_enabled() -> bool:
    """True when this instance is actually running a recruited study.

    Either credential is enough: a study can be run on the return URL alone (it embeds the code),
    or on a typed code alone where the platform has no return mechanism.
    """
    return bool(config.STUDY_COMPLETION_CODE or config.STUDY_COMPLETION_URL)


def completion_state(db: Session, session_id: str) -> dict:
    """Progress toward completion for one voter.

    BOTH `code` and `return_url` are None until the ballot requirement is met. The release
    decision lives here rather than in the template, so a future template edit cannot render
    either one early — and the URL matters as much as the code, because it CARRIES the code in
    its query string. Leaking the button is leaking the code, just wearing a coat.
    """
    required = max(1, int(config.STUDY_REQUIRED_VOTES))
    vs = db.get(VoterSession, session_id)
    cast = int(vs.n_votes or 0) if vs else 0
    done = cast >= required
    return {
        "cast": cast,
        "required": required,
        "remaining": max(0, required - cast),
        "complete": done,
        "cohort": (vs.cohort if vs else None),
        "code": config.STUDY_COMPLETION_CODE if done else None,
        "return_url": config.STUDY_COMPLETION_URL if done else None,
    }
