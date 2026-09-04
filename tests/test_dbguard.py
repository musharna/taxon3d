"""app.dbguard: one shared --apply gate for every script that writes a database.

The prod-write guard used to be a docstring ("NEVER point BIO3D_DATABASE_URL at the study DB")
copied into scripts; a docstring cannot refuse. These tests pin the helper's contract with a
positive control (apply + non-study returns) beside each refusal, so a broken helper cannot
read as "everything is refused, therefore safe".
"""

from __future__ import annotations

import argparse

import pytest

from app import config, dbguard


def _ns(**kw) -> argparse.Namespace:
    base = {"apply": False, "allow_study": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_add_write_target_args_defaults_to_dry_run():
    ap = argparse.ArgumentParser()
    dbguard.add_write_target_args(ap)
    a = ap.parse_args([])
    assert a.apply is False and a.allow_study is False
    assert ap.parse_args(["--apply", "--allow-study"]).apply is True


def test_apply_on_non_study_url_returns_and_names_the_target(monkeypatch, capsys):
    """Positive control: the honest path proceeds and prints WHERE it is about to write."""
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////tmp/copy-of-prod.db")
    dbguard.confirm_write_target(_ns(apply=True), purpose="reseed gold pairs")
    out = capsys.readouterr().out
    assert "sqlite:////tmp/copy-of-prod.db" in out
    assert "reseed gold pairs" in out


def test_without_apply_refuses_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////tmp/copy-of-prod.db")
    with pytest.raises(SystemExit) as e:
        dbguard.confirm_write_target(_ns(apply=False), purpose="reseed gold pairs")
    assert e.value.code == 2
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert "--apply" in text and "sqlite:////tmp/copy-of-prod.db" in text


def test_study_url_refuses_even_with_apply(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////data/study/arena-study.db")
    with pytest.raises(SystemExit) as e:
        dbguard.confirm_write_target(_ns(apply=True), purpose="score")
    assert e.value.code == 2


def test_study_url_refusal_names_the_override(monkeypatch, capsys):
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////data/study/arena-study.db")
    with pytest.raises(SystemExit):
        dbguard.confirm_write_target(_ns(apply=True), purpose="score")
    assert "--allow-study" in capsys.readouterr().err


def test_study_url_with_allow_study_and_apply_proceeds(monkeypatch):
    """Positive control for the study gate: both flags together is the deliberate path."""
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////data/study/arena-study.db")
    dbguard.confirm_write_target(_ns(apply=True, allow_study=True), purpose="score")


def test_password_is_redacted_in_the_printed_url(monkeypatch, capsys):
    monkeypatch.setattr(
        config, "DATABASE_URL", "postgresql+psycopg://arena:s3cret@db.example/arena"
    )
    dbguard.confirm_write_target(_ns(apply=True), purpose="import")
    out = capsys.readouterr().out
    assert "s3cret" not in out and "arena:***@db.example" in out
