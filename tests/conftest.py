"""Tests never touch real data.

crossfade.config reads CROSSFADE_DATA_DIR once, at its first import. Several test modules set it
with setdefault before importing crossfade – too late when another module imported crossfade
first (running a few files together), and then the default ./data, the real database of a local
install, was used. So it is set here, before any test module is imported; a module that needs its
own folder (test_api) still sets it first thing.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ["CROSSFADE_DATA_DIR"] = tempfile.mkdtemp(prefix="crossfade-tests-")

REAL_DATA = (Path(__file__).resolve().parent.parent / "data").resolve()


@pytest.fixture(autouse=True, scope="session")
def _never_the_real_data():
    from crossfade.config import config
    assert config.data_dir.resolve() != REAL_DATA, "tests must not run against ./data"
