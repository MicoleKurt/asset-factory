"""Skip the tests that need the client's variable sheet when it is absent.

tests/test_sheet.py reads inputs/site-variable-sheet.xlsx, which is the client's
own workbook and is not published with this repository. Everything else runs
without it.
"""
import pathlib

import pytest

SHEET = pathlib.Path(__file__).resolve().parents[1] / "inputs" / "site-variable-sheet.xlsx"


def pytest_collection_modifyitems(config, items):
    if SHEET.exists():
        return
    skip = pytest.mark.skip(reason="needs the client's site-variable-sheet.xlsx (not published)")
    for item in items:
        if item.path.name == "test_sheet.py":
            item.add_marker(skip)
