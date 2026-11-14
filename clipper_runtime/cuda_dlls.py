"""Register locally installed CUDA DLL folders without changing PATH."""
from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path
from typing import Any

_DLL_DIRECTORY_HANDLES: list[Any] = []
_REGISTERED: set[Path] = set()
_LOADED_DLLS: dict[Path, Any] = {}


def register_cuda_dll_directories(site_packages: list[Path] | None = None) -> list[Path]:
    add_directory = getattr(os, "add_dll_directory", None)
    if os.name != "nt" or add_directory is None:
        return []
    roots = site_packages or [Path(item) for item in sys.path if item and item.lower().endswith("site-packages")]
    directories = sorted({folder.resolve() for root in roots for folder in root.glob("nvidia/*/bin") if folder.is_dir()})
    if site_packages is None:
        project = Path(__file__).resolve().parent.parent
        candidates = [project / ".runtime" / "cuda" / "bin",
                      project / ".runtime" / "llama.cpp" / "b12284" / "bin"]
        candidates.extend(root / "ctranslate2" for root in roots)
        directories.extend(folder.resolve() for folder in candidates if folder.is_dir())
    registered = []
    for folder in directories:
        # CTranslate2's native LoadLibrary calls may not use Python's DLL search flags.
        if site_packages is None:
            for name in ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll"):
                library = folder / name
                if library.is_file() and library not in _LOADED_DLLS:
                    try:
                        _LOADED_DLLS[library] = ctypes.WinDLL(str(library))
                    except OSError:
                        pass
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
