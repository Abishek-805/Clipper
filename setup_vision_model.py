"""Download and verify pinned SmolVLM2 files without importing PyTorch."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from vision import MODEL_ID, MODEL_REVISION, REQUIRED_MODEL_FILES, cached_model_path, verify_model_snapshot

ALLOW_PATTERNS = ["*.json", "*.model", "*.txt", "*.safetensors", "*.py", "*.tiktoken"]


def download_model_snapshot(allow_download: bool = False, hub=None) -> Path:
    if not allow_download:
        raise PermissionError("Model download requires the explicit --allow-download flag.")
    if hub is None:
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.constants import HF_HUB_CACHE
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install huggingface_hub with the active .venv\\Scripts\\python.exe -m pip install huggingface_hub") from exc
        cache_root = Path(HF_HUB_CACHE)
    else:
        snapshot_download, cache_root = hub
    existing = cached_model_path()
    if verify_model_snapshot(existing):
        print(f"MODEL_SNAPSHOT_VERIFIED {existing}", flush=True)
        return existing

    print(f"Checking pinned revision {MODEL_REVISION} and required disk space…", flush=True)
    try:
        plan = snapshot_download(MODEL_ID, revision=MODEL_REVISION, allow_patterns=ALLOW_PATTERNS, dry_run=True)
        if not plan:
            raise RuntimeError("Hugging Face returned an empty download plan.")
        revisions = {item.commit_hash for item in plan}
        if revisions != {MODEL_REVISION}:
            raise RuntimeError(f"Hub resolved an unexpected model revision: {sorted(revisions)}")
        planned_names = {Path(item.filename).name for item in plan}
        missing = set(REQUIRED_MODEL_FILES) - planned_names
        if missing:
            raise RuntimeError(f"Pinned repository is missing required inference files: {', '.join(sorted(missing))}")
        needed = sum(item.file_size for item in plan) + 512 * 1024 * 1024
        cache_root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(cache_root).free
        if free < needed:
            raise OSError(f"Not enough free disk space: need about {needed / (1024**3):.2f} GiB including cache margin; have {free / (1024**3):.2f} GiB.")
        print(f"Downloading {len(plan)} inference files ({sum(item.file_size for item in plan) / (1024**3):.2f} GiB); Hugging Face progress follows.", flush=True)
        snapshot = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION, allow_patterns=ALLOW_PATTERNS,
                                         local_files_only=False))
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if any(code in message for code in ("401", "403", "RepositoryNotFound", "GatedRepo")):
            message += " Check network access and Hugging Face access permissions for the pinned repository."
        raise RuntimeError(f"Pinned SmolVLM2 download failed: {message}") from exc

    if snapshot.name != MODEL_REVISION or not verify_model_snapshot(snapshot):
        raise RuntimeError(f"Download finished but snapshot verification failed for revision {MODEL_REVISION}: {snapshot}")
    print(f"MODEL_SNAPSHOT_VERIFIED {snapshot}", flush=True)
    return snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-download", action="store_true", required=True,
                        help="Explicitly authorize download of the pinned model files")
    args = parser.parse_args(argv)
    try:
        download_model_snapshot(args.allow_download)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
