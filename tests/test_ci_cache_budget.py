"""No workflow job saves pip's download cache.

A repo's Actions caches share 10 GB and GitHub evicts the least recently used
entry first. `setup-python`'s `cache: pip` saved 2.9 GB of torch and CUDA wheels
under a key that hashes the requirements files, once per pull request and once
per merge that touched one. That pushed the repo over the limit and the entry
evicted was the nightly's mutation results (0.3 MB, read once a day), so the
nightly of 2026-11-16 found none of the results saved the day before.

The check reads the workflow files, so it cannot see the cache list itself; the
nightly's "no results restored" warning is the live signal.
"""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def pip_cache_steps(workflow_text: str) -> tuple[int, list[str]]:
    """(setup-python steps seen, the jobs whose setup-python step sets `cache`)."""
    doc = yaml.safe_load(workflow_text)
    seen = 0
    offenders = []
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith("actions/setup-python@"):
                seen += 1
                if (step.get("with") or {}).get("cache"):
                    offenders.append(job_name)
    return seen, offenders


_CACHING = """
jobs:
  test:
    steps:
      - uses: actions/setup-python@0000000000000000000000000000000000000000
        with:
          python-version: "3.12"
          cache: pip
  lint:
    steps:
      - uses: actions/setup-python@0000000000000000000000000000000000000000
        with:
          python-version: "3.12"
"""


def test_no_job_saves_pips_download_cache():
    # The check flags a job that caches and passes one that does not.
    assert pip_cache_steps(_CACHING) == (2, ["test"])

    seen = 0
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        n, bad = pip_cache_steps(wf.read_text(encoding="utf-8"))
        seen += n
        offenders += [f"{wf.name}: {job}" for job in bad]
    # ci.yml's lint, test and deploy-guards jobs each set Python up this way; a
    # scan that saw none of them judged nothing.
    assert seen >= 3, f"only {seen} setup-python steps found under {WORKFLOWS}"
    assert offenders == [], (
        "setup-python `cache: pip` saves pip's download cache (2.9 GB with torch) "
        "on every requirements change and evicts the nightly's mutation results; "
        "see the comment in ci.yml's test job: " + ", ".join(offenders)
    )
