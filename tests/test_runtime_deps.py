"""The public web instance must not need the research/scoring stack.

`requirements.txt` is what a deployer installs (deploy/README.md step 4). Everything the serving
app can reach at request time belongs there; everything else belongs in requirements-research.txt
or requirements-dev.txt. The expensive member of that second group is open_clip_torch, which drags
torch plus the whole NVIDIA CUDA stack (multiple GB) onto a web host that never imports it.

Nothing structural stops a future edit from adding `import torch` at the top of an app module and
silently re-coupling the two. This test is that stop: it boots the real ASGI app in a SUBPROCESS
(sys.modules is process-global, so an in-process check would be corrupted by whichever test
imported tifffile first), serves the public routes, and asserts the heavy modules stayed unloaded.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Import name -> distribution name, for the cases where PyPI and `import` disagree. Anything not
# listed here is assumed to import under its distribution name (fastapi, httpx, numpy, ...).
_IMPORT_TO_DIST = {
    "PIL": "pillow",
    "sqlalchemy": "SQLAlchemy",
    "jinja2": "Jinja2",
    "multipart": "python-multipart",
    "yaml": "PyYAML",
    "skimage": "scikit-image",
    "open_clip": "open_clip_torch",
}


def _pinned_distributions(*files: str) -> set[str]:
    """Distribution names pinned in the given requirements files, lower-cased, extras stripped."""
    names: set[str] = set()
    for f in files:
        for line in (REPO_ROOT / f).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if m:
                names.add(m.group(1).lower())
    return names


def _top_level_third_party_imports() -> dict[str, set[str]]:
    """Module name -> app files that import it at module top level (not lazily, not in strings)."""
    found: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "app").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                if root in sys.stdlib_module_names or root in ("app", "__future__"):
                    continue
                found.setdefault(root, set()).add(path.name)
    return found


# Offline-only: the request path never reaches these. Kept out of requirements.txt.
#   torch/open_clip -> app.species_id, reached only via reference_qa <- input_verify <- a script
#   scipy/skimage/nibabel/tifffile -> app.volume_convert, imported only by scripts + tests
#   fast_simplification -> trimesh's decimation backend for app.mesh_convert (scripts + tests)
#   anthropic -> the VLM judge; not imported anywhere under app/
FORBIDDEN_AT_RUNTIME = [
    "torch",
    "open_clip",
    "scipy",
    "skimage",
    "nibabel",
    "tifffile",
    "fast_simplification",
    "anthropic",
]

# Public-instance posture: scoring off. These are the routes deploy/README.md smoke-tests.
_PROBE = """
import json, os, sys, tempfile
tmp = tempfile.mkdtemp(prefix="bio3d_test_runtimedeps_")
os.environ["BIO3D_DATA_DIR"] = tmp
os.environ["BIO3D_DB_PATH"] = os.path.join(tmp, "bio3d_test_runtime.db")
os.environ.pop("BIO3D_DATABASE_URL", None)
os.environ["BIO3D_RECON_SCORER_URL"] = ""
# Public deploys must declare their base URL (2026-07-27 audit guard).
os.environ["BIO3D_PUBLIC_BASE_URL"] = "https://arena.example"
sys.path.insert(0, %(root)r)

from app.database import Base, engine
import app.models  # noqa: F401

Base.metadata.create_all(bind=engine)

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    for route in ["/", "/leaderboard", "/coverage", "/terms", "/licenses", "/arena"]:
        c.get(route)

print("BIO3D_LOADED=" + json.dumps(sorted(m for m in %(forbidden)r if m in sys.modules)))
"""


def test_serving_the_public_app_never_imports_the_research_stack():
    probe = _PROBE % {"root": str(REPO_ROOT), "forbidden": FORBIDDEN_AT_RUNTIME}
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("BIO3D_LOADED=")]
    assert line, f"probe produced no verdict:\n{proc.stdout}\n{proc.stderr}"
    loaded = json.loads(line[-1].split("=", 1)[1])
    assert loaded == [], (
        f"serving the public app loaded research-only modules: {loaded}. Either import them "
        "lazily (inside the function that needs them, as app.species_id does), or move the "
        "dependency into requirements.txt and update deploy/README.md — but note that puts the "
        "full CUDA stack on the public web host."
    )


def test_requirements_files_partition_the_dependencies():
    """Runtime / research / dev are layered, and the heavy stack is NOT in the runtime file."""
    runtime = (REPO_ROOT / "requirements.txt").read_text()
    research = (REPO_ROOT / "requirements-research.txt").read_text()
    dev = (REPO_ROOT / "requirements-dev.txt").read_text()

    for pkg in ("open_clip_torch", "anthropic", "scipy", "scikit-image", "nibabel", "tifffile"):
        assert pkg not in runtime, f"{pkg} is research-only; it must not be in requirements.txt"
    for pkg in ("pytest", "ruff"):
        assert pkg not in runtime, f"{pkg} is a dev tool; it must not be in requirements.txt"

    # The web app genuinely needs these — /api/submit reaches ingest -> trimesh/structural -> numpy.
    for pkg in ("fastapi", "SQLAlchemy", "trimesh", "numpy", "pillow"):
        assert pkg in runtime, f"{pkg} is request-reachable and must stay in requirements.txt"

    # Layered, so a research or dev install is a superset of runtime rather than a rival list.
    assert "-r requirements.txt" in research
    assert "-r requirements-research.txt" in dev


def test_every_top_level_app_import_is_installable_from_the_runtime_files():
    """A module-level `import x` in app/*.py must resolve from requirements.txt + -scale.txt.

    The subprocess probe above only exercises the public routes, so a module that is imported at
    top level by a file the public routes never touch (app.recon_client, app.image3d, app.client)
    is invisible to it — and it was: `httpx` sat at the top of three app modules while living only
    in requirements-dev.txt. The Docker image installs runtime + scale and nothing else, so any
    import path that reached one of those modules would have died with ModuleNotFoundError in
    production and nowhere else.

    The stdlib is excluded by `sys.stdlib_module_names`; lazy (inside-function) imports are
    excluded by walking only `tree.body`, which is exactly the lazy-import escape hatch the
    docstring of this file prescribes for research-only modules.
    """
    pinned = _pinned_distributions("requirements.txt", "requirements-scale.txt")
    missing = {}
    for module, files in _top_level_third_party_imports().items():
        dist = _IMPORT_TO_DIST.get(module, module).lower()
        if dist not in pinned:
            missing[module] = sorted(files)
    assert not missing, (
        f"imported at module top level under app/ but pinned in neither requirements.txt nor "
        f"requirements-scale.txt: {missing}. Pin it (runtime if the serving app can reach it), or "
        "import it lazily inside the function that needs it."
    )

    # Positive control: the scanner must actually see the imports it is guarding. If the AST walk
    # silently found nothing, the assertion above would pass on an empty dict.
    seen = _top_level_third_party_imports()
    assert "fastapi" in seen and "main.py" in seen["fastapi"]
    assert "sqlalchemy" in seen and "database.py" in seen["sqlalchemy"]
