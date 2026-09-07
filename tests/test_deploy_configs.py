"""The two deploy-side additions from the 2026-09-06 audit are config, and config drifts silently.
Parse them and pin the load-bearing values."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_log_shipper_targets_this_org_and_uses_flys_image():
    cfg = tomllib.loads((ROOT / "deploy" / "log-shipper" / "fly.toml").read_text())
    assert cfg["app"] == "bio3d-log-shipper"
    assert cfg["build"]["image"].startswith("flyio/log-shipper")
    assert cfg["env"]["SUBJECT"] == "logs.>"


def test_backup_workflow_is_scheduled_and_never_prints_secrets():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "backup.yml").read_text())
    on = wf.get("on") or wf.get(True)  # PyYAML parses a bare `on:` key as boolean True
    assert "schedule" in on and "workflow_dispatch" in on
    steps = wf["jobs"]["backup"]["steps"]
    text = (ROOT / ".github" / "workflows" / "backup.yml").read_text()
    for secret in ("FLY_API_TOKEN", "AWS_SECRET_ACCESS_KEY", "BACKUP_S3_ENDPOINT"):
        assert f"secrets.{secret}" in text
    # no step echoes its environment
    assert not any("env" in (st.get("run") or "").split() for st in steps)
    # the boto3 pin matches the scale requirements, so CI and the image agree on the client
    scale = (ROOT / "requirements-scale.txt").read_text()
    pin = [ln for ln in scale.splitlines() if ln.startswith("boto3==")][0].split()[0]
    assert pin in text, pin
