"""Register locally installed CUDA DLL folders without changing PATH."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_DLL_DIRECTORY_HANDLES: list[Any] = []
_REGISTERED: set[Path] = set()


def register_cuda_dll_directories(site_packages: list[Path] | None = None) -> list[Path]:
    add_directory = getattr(os, "add_dll_directory", None)
    if os.name != "nt" or add_directory is None:
        return []
    roots = site_packages or [Path(item) for item in sys.path if item and item.lower().endswith("site-packages")]
    directories = sorted({folder.resolve() for root in roots for folder in root.glob("nvidia/*/bin") if folder.is_dir()})
    registered = []
    for folder in directories:
        if folder in _REGISTERED:
            registered.append(folder)
            continue
        try:
            _DLL_DIRECTORY_HANDLES.append(add_directory(str(folder)))
            _REGISTERED.add(folder)
            registered.append(folder)
        except OSError:
            continue
    return registered
