from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper_runtime import cuda_dlls


class CudaDllDirectoryTests(unittest.TestCase):
    def setUp(self):
        cuda_dlls._DLL_DIRECTORY_HANDLES.clear()
        cuda_dlls._REGISTERED.clear()

    def test_registers_cuda_package_bin_directories_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cublas = root / "nvidia" / "cublas" / "bin"
            cudnn = root / "nvidia" / "cudnn" / "bin"
            cublas.mkdir(parents=True)
            cudnn.mkdir(parents=True)
            with patch.object(os, "add_dll_directory", side_effect=lambda path: path) as add:
                first = cuda_dlls.register_cuda_dll_directories([root])
                second = cuda_dlls.register_cuda_dll_directories([root])
            self.assertEqual({cublas.resolve(), cudnn.resolve()}, set(first))
            self.assertEqual(first, second)
            self.assertEqual(2, add.call_count)
            self.assertEqual(2, len(cuda_dlls._DLL_DIRECTORY_HANDLES))

    def test_native_libraries_are_preloaded_once_by_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "site-packages"
            folder = root / "nvidia" / "cublas" / "bin"
            folder.mkdir(parents=True)
            for name in ("cublasLt64_12.dll", "cublas64_12.dll"):
                (folder / name).write_bytes(b"fixture")
            cuda_dlls._LOADED_DLLS.clear()
            with patch.object(cuda_dlls.sys, "path", [str(root)]), patch.object(os, "add_dll_directory"), patch.object(cuda_dlls.ctypes, "WinDLL") as load:
                cuda_dlls.register_cuda_dll_directories()
                cuda_dlls.register_cuda_dll_directories()
            loaded = [Path(call.args[0]) for call in load.call_args_list]
            self.assertEqual([folder / "cublasLt64_12.dll", folder / "cublas64_12.dll"], [p for p in loaded if p.parent == folder])
            self.assertEqual(len(loaded), len(set(loaded)))

    def test_missing_packages_are_a_safe_noop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(os, "add_dll_directory") as add:
            self.assertEqual([], cuda_dlls.register_cuda_dll_directories([Path(tmp)]))
            add.assert_not_called()

    def test_non_windows_platform_is_a_safe_noop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cuda_dlls.os, "name", "posix"):
            self.assertEqual([], cuda_dlls.register_cuda_dll_directories([Path(tmp)]))
