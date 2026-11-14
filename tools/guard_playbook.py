"""Read-only integration guard for the supplied playbook and protected runtime files."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def guard(root, baseline, integrity=None):
    errors = []
    manifest = json.loads((root/"reports/editorial-playbook-vendor.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest() != expected:
            errors.append("Modified vendor asset: " + name)
    if (root/"clipper/__init__.py").exists():
        errors.append("clipper package would shadow clipper.py")
    protected = ["clipper.py", "main.py", "model_manager.py", "vision.py", "clipper_runtime", "nightly.py",
                 "reframing.py", "captions.py", "review_workspace", "Shorts_Output", "models", ".runtime", "vlc-help.txt"]
    changed = subprocess.run(["git", "diff", "--name-only", baseline, "--", *protected], cwd=root,
                             capture_output=True, text=True, check=True).stdout.splitlines()
    errors.extend("Changed protected tracked path: " + name for name in changed)
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    if branch != "feature/personal-v1-playbook":
        errors.append("Unexpected branch: " + branch)
    if integrity:
        for name, expected in json.loads(integrity.read_text(encoding="utf-8")).items():
            with Path(name).open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                    errors.append("Changed preserved local file: " + name)
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--integrity", type=Path)
    args = parser.parse_args()
    errors = guard(Path(__file__).resolve().parents[1], args.baseline, args.integrity)
    print("\n".join(errors) if errors else "PASS: vendor, branch and protected-file guards")
    sys.exit(bool(errors))
