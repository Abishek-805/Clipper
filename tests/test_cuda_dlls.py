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

    def test_missing_packages_are_a_safe_noop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(os, "add_dll_directory") as add:
            self.assertEqual([], cuda_dlls.register_cuda_dll_directories([Path(tmp)]))
            add.assert_not_called()

    def test_non_windows_platform_is_a_safe_noop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cuda_dlls.os, "name", "posix"):
            self.assertEqual([], cuda_dlls.register_cuda_dll_directories([Path(tmp)]))
