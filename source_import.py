"""Stream browser-selected media to local storage; reuse verified known originals."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path, PureWindowsPath

from clipper import VIDEO_EXTENSIONS, probe


def import_source(stream, length: int, filename: str, imports: Path, known_sources=()):
    if not isinstance(filename, str) or PureWindowsPath(filename).name != filename:
        raise ValueError("Choose one video file, without a directory in its name")
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS or length <= 0:
        raise ValueError("Choose a non-empty MP4, MKV, MOV, AVI, WebM or M4V video")
    imports.mkdir(parents=True, exist_ok=True)
    if length > shutil.disk_usage(imports).free - 1024**3:
        raise ValueError("Not enough free space for this local import; paste the original path instead")
    handle, raw = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=imports)
    temporary = Path(raw)
    digest = hashlib.sha256()
    try:
        with os.fdopen(handle, "wb") as output:
            remaining = length
            while remaining:
                chunk = stream.read(min(1024**2, remaining))
                if not chunk:
                    raise ValueError("File transfer was interrupted; choose the file again")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        fingerprint = digest.hexdigest()
        for raw_candidate in dict.fromkeys(known_sources):
            try:
                candidate = Path(raw_candidate).resolve()
                if candidate.is_file() and candidate.suffix.lower() == suffix and candidate.stat().st_size == length:
                    with candidate.open("rb") as original:
                        matches = hashlib.file_digest(original, "sha256").hexdigest() == fingerprint
                    if matches:
                        probe(candidate)
                        return {"source": str(candidate), "reused": True, "sha256": fingerprint}
            except (OSError, ValueError):
                continue
        probe(temporary)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
        name = name[:-len(suffix)][:180-len(suffix)] + suffix
        if (os.path.isreserved(name) if hasattr(os.path, "isreserved") else PureWindowsPath(name).is_reserved()):
            name = "_" + name
        folder = imports / fingerprint
        folder.mkdir(exist_ok=True)
        target = folder / name
        if target.exists():
            with target.open("rb") as existing:
                if hashlib.file_digest(existing, "sha256").hexdigest() != fingerprint:
                    raise ValueError("An existing imported file was modified; paste a new source path")
        else:
            os.replace(temporary, target)
        return {"source": str(target.resolve()), "reused": False, "sha256": fingerprint}
    finally:
        temporary.unlink(missing_ok=True)
