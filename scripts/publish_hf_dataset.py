"""Upload an exported Taxon3D dataset tree to the Hugging Face Hub.

Separate from `export_hf_dataset.py` on purpose: that script decides WHICH BYTES may leave the
machine, and this one only moves an already-cleared tree. Keeping the upload out of the exporter
means no future change to publishing can quietly widen what the gate chain admits.

Creates the repo PRIVATE. Flipping a dataset public is the one step here with no undo — the Hub
indexes fast and a fetched copy cannot be recalled — so it is left to a human in the web UI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Files that must exist in a tree before it is allowed to upload. A partial export (interrupted
#: mesh copy, missing card) is otherwise indistinguishable from a complete one at this layer, and
#: the failure would only be visible after the bytes were already public.
REQUIRED = ("README.md", "TRANSFORM.md", "manifest.json", "outputs.jsonl", "admissibility.jsonl")


def resolve_write_token(
    env: Mapping[str, str] | None = None, token_file: Path | None = None
) -> str:
    """Return a token that can actually write, preferring the stored CLI token over `$HF_TOKEN`.

    This inverts `huggingface_hub`'s own precedence, deliberately. That library reads `$HF_TOKEN`
    first, and a machine can easily export a READ-scoped token there while a WRITE-scoped one sits
    in `~/.cache/huggingface/token` from an earlier `hf auth login` — which is exactly the state
    this repo's dev machine was in. Under the library's order every upload 403s, and the error
    names permissions rather than precedence, so it reads as a broken account rather than the
    wrong one of two tokens being picked up.

    The stored file wins because `hf auth login` is the deliberate act of granting write access;
    an exported env var is more often inherited from a shell profile nobody has revisited. Callers
    that want the env var can pass `--token-file` pointing elsewhere.
    """
    env = os.environ if env is None else env
    token_file = Path.home() / ".cache/huggingface/token" if token_file is None else token_file

    if token_file.exists():
        stored = token_file.read_text().strip()
        if stored:
            return stored
    from_env = (env.get("HF_TOKEN") or "").strip()
    if from_env:
        return from_env
    raise RuntimeError(
        f"no Hugging Face token found: {token_file} is absent or empty and $HF_TOKEN is unset. "
        "Run `hf auth login` with a WRITE-scoped token."
    )


def assert_tree_complete(tree: Path) -> dict:
    """Raise unless `tree` looks like a finished export; return its manifest.

    Checked here rather than trusted from the exporter because the two run at different times: a
    tree can be half-deleted, half-copied, or simply the wrong directory by the time anyone
    uploads it.
    """
    if not tree.is_dir():
        raise RuntimeError(f"{tree} is not a directory")
    missing = [name for name in REQUIRED if not (tree / name).exists()]
    if missing:
        raise RuntimeError(f"{tree} is not a complete export — missing {', '.join(missing)}")

    manifest = json.loads((tree / "manifest.json").read_text())
    meshes = sorted((tree / "meshes").glob("*.glb")) if (tree / "meshes").is_dir() else []
    expected = manifest.get("counts", {}).get("meshes")
    if expected is None:
        raise RuntimeError(
            "manifest.json has no counts.meshes — refusing to upload a tree I cannot verify"
        )
    if len(meshes) != expected:
        raise RuntimeError(
            f"mesh count mismatch: manifest says {expected}, tree holds {len(meshes)}. "
            "Re-run the export rather than uploading a partial corpus."
        )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="data/hf_export", help="exported dataset directory")
    ap.add_argument("--repo", required=True, help="e.g. musharna/taxon3d-corpus-v1")
    ap.add_argument("--token-file", default=None, help="override the stored-token path")
    ap.add_argument("--dry-run", action="store_true", help="verify and print, upload nothing")
    args = ap.parse_args()

    tree = Path(args.tree)
    manifest = assert_tree_complete(tree)
    acct = manifest.get("accounting", {})
    print(f"tree      : {tree}")
    print(f"counts    : {manifest.get('counts')}")
    print(f"accounting: {acct}")

    token = resolve_write_token(token_file=Path(args.token_file) if args.token_file else None)

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    who = api.whoami()
    role = ((who.get("auth") or {}).get("accessToken") or {}).get("role")
    print(f"user      : {who.get('name')}  token role: {role}")
    if role != "write":
        raise RuntimeError(
            f"token role is {role!r}, not 'write' — create a WRITE token at "
            "https://huggingface.co/settings/tokens and `hf auth login` with it."
        )

    if args.dry_run:
        print("\ndry run — nothing uploaded")
        return 0

    url = api.create_repo(repo_id=args.repo, repo_type="dataset", private=True, exist_ok=True)
    print(f"repo      : {url} (PRIVATE)")
    api.upload_folder(
        repo_id=args.repo,
        repo_type="dataset",
        folder_path=str(tree),
        commit_message="Taxon3D corpus: admissibility-gated organism meshes and verdicts",
    )
    print(f"\nuploaded. Review at https://huggingface.co/datasets/{args.repo}")
    print("It is PRIVATE. Flip it public yourself in Settings once you have looked it over.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
