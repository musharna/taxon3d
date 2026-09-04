"""The conftest safety guard that refuses to run tests against a non-throwaway DB.

Born from the 2026-06-28 incident: pytest run with BIO3D_DATABASE_URL=…/arena-study.db
dropped/recreated tables and wiped the study DB.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config import is_safe_test_db_target

_REPO = Path(__file__).resolve().parent.parent


def test_is_safe_test_db_target_allows_throwaways():
    assert is_safe_test_db_target(None) is True  # unset → conftest isolates to a temp dir
    assert is_safe_test_db_target("") is True
    assert is_safe_test_db_target("sqlite:///:memory:") is True
    assert is_safe_test_db_target("sqlite:////tmp/bio3d_test_x/arena.db") is True
    assert is_safe_test_db_target("sqlite:///./test_arena.db") is True


def test_is_safe_test_db_target_allows_test_as_a_path_token():
    # "test" as a whole path/filename token, bounded by / _ - . or a string end.
    assert is_safe_test_db_target("sqlite:///./test.db") is True
    assert is_safe_test_db_target("/home/u/bio3d-arena/tests/fixtures/arena.db") is True
    assert is_safe_test_db_target("/home/u/scratch/test-arena.db") is True
    assert is_safe_test_db_target("test") is True


def test_is_safe_test_db_target_rejects_real_dbs():
    assert (
        is_safe_test_db_target("sqlite:////home/u/bio3d-arena/data/study/arena-study.db") is False
    )
    assert is_safe_test_db_target("sqlite:////srv/bio3d/data/arena-prod.db") is False
    assert is_safe_test_db_target("postgresql://host/bio3d_production") is False


def test_is_safe_test_db_target_rejects_test_as_a_substring():
    # "test" inside another word is not a throwaway marker: `latest` and `contest` are real
    # release/data directories, and a substring match would have waved them through.
    assert is_safe_test_db_target("/home/u/bio3d-arena/data/latest/arena.db") is False
    assert is_safe_test_db_target("sqlite:////home/x/contest/arena.db") is False
    assert is_safe_test_db_target("postgresql://host/attestation") is False


def test_conftest_guard_covers_every_db_destination_var():
    """The guard must watch the same variables app.envfile refuses to take from a .env —
    BIO3D_DATA_DIR included, because app.config derives DB_PATH from DATA_DIR."""
    import conftest
    from app.envfile import DB_DESTINATION_VARS

    assert set(conftest.GUARDED_DB_VARS) == set(DB_DESTINATION_VARS)
    assert "BIO3D_DATA_DIR" in conftest.GUARDED_DB_VARS


def test_conftest_aborts_on_dangerous_data_dir():
    """BIO3D_DATA_DIR alone selects the DB (DB_PATH = DATA_DIR/arena.db), so it must be guarded."""
    env = {
        "PATH": "/usr/bin:/bin",
        "BIO3D_DATA_DIR": "/home/nonexistent/bio3d-arena/data",  # fake + unsafe
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import conftest"],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}: {proc.stderr}"
    assert "REFUSING TO RUN TESTS" in proc.stderr
    assert "BIO3D_DATA_DIR" in proc.stderr


def test_conftest_accepts_throwaway_data_dir(tmp_path):
    """Positive control for the guard above: a tmp data dir must still be accepted."""
    env = {"PATH": "/usr/bin:/bin", "BIO3D_DATA_DIR": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-c", "import conftest"],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"tmp data dir should be accepted: {proc.stderr}"


def test_conftest_aborts_on_dangerous_db_env():
    """Real-execution check: importing conftest with a real-DB env must hard-exit non-zero.

    Uses a fake non-existent path containing no throwaway marker (no real DB is touched).
    """
    env = {
        "PATH": "/usr/bin:/bin",
        # fake + unsafe (no :memory:/tmp/test marker) → the guard must fire
        "BIO3D_DATABASE_URL": "sqlite:////home/nonexistent/data/study/arena-study.db",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import conftest"],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}: {proc.stderr}"
    assert "REFUSING TO RUN TESTS" in proc.stderr


def test_conftest_imports_clean_with_safe_env():
    """The guard must NOT fire for a normal (unset) test run."""
    env = {"PATH": "/usr/bin:/bin"}  # no BIO3D_DATABASE_URL → safe
    proc = subprocess.run(
        [sys.executable, "-c", "import conftest"],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"clean import should exit 0: {proc.stderr}"
