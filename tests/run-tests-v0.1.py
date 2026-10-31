#!/usr/bin/env python3
"""Run every tests/test-*.py (file names carry versions, so unittest discovery cannot import them by name)."""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
suite = unittest.TestSuite()
for f in sorted(HERE.glob("test-*.py")):
    spec = importlib.util.spec_from_file_location("t_" + f.stem.replace("-", "_").replace(".", "_"), f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(mod))
res = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if res.wasSuccessful() else 1)
