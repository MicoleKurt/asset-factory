#!/usr/bin/env python
"""Launcher so the workflow runs without setting PYTHONPATH.

    python asset-factory.py validate
    python asset-factory.py plan
    python asset-factory.py apply --confirm

Equivalent to `python -m asset_factory.cli ...` with src/ on the path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from asset_factory.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
