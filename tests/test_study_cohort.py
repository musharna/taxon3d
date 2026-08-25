"""Recruited-study support: cohort tagging and the completion gate.

Why this exists: the arena has never had an organic vote, so every discovery hypothesis about it
is unfalsifiable — "nobody came" and "they came and bounced" look identical from the vote count.
A paid cohort (Prolific) answers that, but only if its votes are SEPARABLE from ambient traffic,
and only if a participant can prove they finished. That is all this module does.

Deliberately NOT here: the third-party participant id. A campaign LABEL is enough to attribute
votes, and per-participant ballots already come from `VoterSession.n_votes`. Storing a
platform-assigned id would add a re-identification surface that buys nothing.
"""

import uuid

from fastapi.testclient import TestClient

from app import config
from app.database import SessionLocal
from app.main import app
from app.models import VoterSession
from app.seed import seed_all


def setup_module(_module):
    """A votable corpus, or every ballot assertion below is vacuous (see test_api.py)."""
    seed_all(force=True)


def _voter():
    """A client with an EXPLICITLY PINNED session id, and that id.

    Pinned rather than letting the cookie round-trip, because the session cookie carries
    `Secure` whenever another test in the run has left `PUBLIC_BASE_URL` set to an https value —
    and TestClient speaks plain http, so the browser jar silently drops it. Every request then
    gets a fresh session, one row is written per request, and these tests fail only in a full
    suite while passing alone. Pinning tests the behaviour that actually matters (same voter,
    repeated requests) without depending on suite-wide config state.
    """
    sid = uuid.uuid4().hex
    c = TestClient(app)
    c.cookies.set("bio3d_session", sid)
    return c, sid


def _cohort_of(session_id):
    with SessionLocal() as db:
        vs = db.get(VoterSession, session_id)
        return vs.cohort if vs else None


def test_campaign_tag_stamps_the_voter_session():
    """A tagged landing must be attributable, or the cohort's votes cannot be told apart."""
    c, sid = _voter()
    r = c.get("/arena?c=pilot-1")
    assert r.status_code == 200
    assert _cohort_of(sid) == "pilot-1"


def test_untagged_visit_leaves_no_cohort():
    """POSITIVE CONTROL for the test above: the stamp must be doing the work, not a default.

    Without this, `cohort` could be set unconditionally and the attribution test would still
    pass — while every ambient visitor was silently counted as a study participant.
    """
    c, sid = _voter()
    assert c.get("/arena").status_code == 200
    assert _cohort_of(sid) is None


def test_first_cohort_wins_and_a_later_tag_cannot_relabel():
    """First write wins. A participant who wanders off and returns via another link stays in
    their original cohort — otherwise the last link clicked rewrites history, and per-cohort
    vote counts stop meaning anything mid-study."""
    c, sid = _voter()
    c.get("/arena?c=pilot-1")
    c.get("/arena?c=pilot-2")
    assert _cohort_of(sid) == "pilot-1"


def test_junk_campaign_labels_are_ignored():
    """The label lands in a DB column and is echoed in analysis output, so it is validated, not
    trusted. A rejected label must leave the session UNTAGGED rather than storing the raw input."""
    c, sid = _voter()
    c.get("/arena?c=" + "x" * 200)
    assert _cohort_of(sid) is None
    c2, sid2 = _voter()
    c2.get("/arena?c=<script>alert(1)</script>")
    assert _cohort_of(sid2) is None


def _vote_n(client, n):
    """Cast up to n real votes; returns how many were accepted."""
    cast = 0
    for _ in range(n):
        r = client.get("/api/next?set=pair")
        if r.status_code != 200:
            break
        cid = r.json().get("comparison_id")
        if cid is None:
            break
        if client.post("/api/vote", json={"comparison_id": cid, "winner": "a"}).status_code == 200:
            cast += 1
    return cast


def test_completion_code_is_withheld_before_the_required_votes(monkeypatch):
    """Paying for 15 ballots and handing out the code at 1 is the failure that costs money."""
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 5)
    c, sid = _voter()
    c.get("/arena?c=pilot-1")
    assert _vote_n(c, 2) >= 1, "fixture cast no votes — the assertions below would be vacuous"
    body = c.get("/study").text
    assert "CODE-XYZ" not in body, "completion code leaked before the ballot requirement was met"


def test_completion_code_is_released_once_the_requirement_is_met(monkeypatch):
    """POSITIVE CONTROL for the withholding test: the gate must also OPEN.

    A gate that never releases the code satisfies every `not in` assertion above while making
    the study impossible to complete, and no negative test can tell the two apart.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 3)
    c, sid = _voter()
    c.get("/arena?c=pilot-1")
    cast = _vote_n(c, 12)
    assert cast >= 3, f"only {cast} votes castable; cannot exercise the release path"
    assert "CODE-XYZ" in c.get("/study").text


def test_no_code_configured_means_no_study_page_claim(monkeypatch):
    """Default-off. An instance that is not running a study must not invite anyone to finish one."""
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 3)
    c, sid = _voter()
    r = c.get("/study")
    assert r.status_code == 404


def test_participant_id_is_never_stored(monkeypatch):
    """PRIVACY CONTROL. Prolific appends PROLIFIC_PID to the study URL; we must ignore it.

    Asserted against the stored row rather than the code, so an accidental future `request.
    query_params` sweep that persisted everything would trip this.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    c, sid = _voter()
    c.get("/arena?c=pilot-1&PROLIFIC_PID=abc123secret&STUDY_ID=s1&SESSION_ID=x9")
    with SessionLocal() as db:
        vs = db.get(VoterSession, sid)
        stored = " ".join(str(v) for v in vars(vs).values() if v is not None)
    assert "abc123secret" not in stored
    assert "s1" not in stored.split()


def test_completion_url_is_withheld_before_the_requirement(monkeypatch):
    """The return link carries the completion code in its query string, so it is gated too.

    Rendering it early would let a participant submit to Prolific without doing the task — the
    same failure as leaking the bare code, just wearing a button.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(
        config, "STUDY_COMPLETION_URL", "https://app.prolific.com/submissions/complete?cc=CODE-XYZ"
    )
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 5)
    c, sid = _voter()
    c.get("/arena?c=pilot-1")
    assert _vote_n(c, 2) >= 1, "fixture cast no votes — the assertion below would be vacuous"
    assert "app.prolific.com" not in c.get("/study").text


def test_completion_url_is_offered_once_complete(monkeypatch):
    """POSITIVE CONTROL, and the reason this exists at all.

    Prolific's own guidance is that an automatic return reduces incomplete submissions, while
    manual codes produce their documented NOCODE failure. The code stays on the page as a
    fallback for when the return does not fire — which is exactly the case NOCODE describes.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(
        config, "STUDY_COMPLETION_URL", "https://app.prolific.com/submissions/complete?cc=CODE-XYZ"
    )
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 3)
    c, sid = _voter()
    c.get("/arena?c=pilot-1")
    cast = _vote_n(c, 12)
    assert cast >= 3, f"only {cast} votes castable; cannot exercise the release path"
    body = c.get("/study").text
    assert "app.prolific.com/submissions/complete" in body
    assert "CODE-XYZ" in body, "the manual code must remain as the fallback when return fails"


def test_arena_tells_a_recruited_participant_where_the_code_is(monkeypatch):
    """Without this the task dead-ends: they vote 15 times and nothing points at /study.

    The progress line is the ONLY thing on the arena page connecting a paid participant to the
    artefact they are paid to return, so it is asserted, not assumed.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 15)
    c, sid = _voter()
    body = c.get("/arena?c=pilot-1").text
    assert "/study" in body, "arena gives a recruited participant no route to their code"
    assert "15" in body


def test_arena_shows_no_study_banner_to_an_ambient_visitor(monkeypatch):
    """CONTROL for the test above: an untagged visitor must see no task framing at all.

    Otherwise every ordinary voter is told they are partway through a paid study — which is both
    confusing and false. Distinguishes "renders for participants" from "renders always".
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 15)
    c, sid = _voter()
    body = c.get("/arena").text
    assert "/study" not in body


def test_vote_response_carries_study_progress(monkeypatch):
    """The counter must update WITHOUT a page navigation, or a participant cannot see they're done.

    Reported by a real pilot participant 2026-08-25: the arena's progress line is rendered
    server-side on page load, while voting happens through fetch("/api/vote") with no reload — so
    it froze at its initial value. The only way to see the true count was to click through to
    /study and come back, and again after finishing to get the code. Some participants overshot
    (one cast 100 ballots against a stated 10) and some would simply not realise they had to
    navigate at all.

    So the vote RESPONSE carries the progress. Asserted server-side because that is the contract
    the client depends on; a client that stops reading it is a visible regression, a server that
    stops sending it is silent.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 3)
    c, sid = _voter()
    c.get("/arena?c=pilot-1")

    r = c.get("/api/next?set=pair")
    assert r.status_code == 200
    cid = r.json()["comparison_id"]
    body = c.post("/api/vote", json={"comparison_id": cid, "winner": "a"}).json()

    assert "study" in body, "vote response carries no progress — the counter cannot update"
    assert body["study"]["cast"] == 1
    assert body["study"]["required"] == 3
    assert body["study"]["complete"] is False
    # The code must NOT ride along on an incomplete ballot: this response reaches the browser
    # after every single vote, so leaking it here would hand it over on ballot one.
    assert "code" not in body["study"]
    assert "CODE-XYZ" not in str(body)


def test_vote_response_omits_study_for_an_ambient_voter(monkeypatch):
    """CONTROL: an untagged voter's response must carry no study block at all.

    Without this, `study` could be attached unconditionally and the test above would still pass
    while every ordinary voter's client was told it was partway through a paid task.
    """
    monkeypatch.setattr(config, "STUDY_COMPLETION_CODE", "CODE-XYZ")
    monkeypatch.setattr(config, "STUDY_REQUIRED_VOTES", 3)
    c, sid = _voter()
    c.get("/arena")
    r = c.get("/api/next?set=pair")
    cid = r.json()["comparison_id"]
    body = c.post("/api/vote", json={"comparison_id": cid, "winner": "a"}).json()
    assert body.get("study") is None
