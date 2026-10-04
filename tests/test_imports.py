"""Every module must import cleanly.

The unit tests exercise the sheet, planner and content logic, so a syntax
error in a report emitter or CLI module could pass a full green run and only
surface when the command was actually invoked. This closes that gap: it walks
the package and imports each module by name.
"""
from __future__ import annotations

import importlib
import pathlib
import pkgutil
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

MODULES = sorted(
    m.name for m in pkgutil.iter_modules([str(SRC / "asset_factory")])
)


def test_package_is_discoverable():
    assert MODULES, "no modules found under src/asset_factory"


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(f"asset_factory.{module}")


def test_reports_render_without_error():
    """Exercise the emitters, which the other tests never call."""
    from asset_factory.htmlreport import markdown_to_html
    from asset_factory.report import _code, _truncate

    html = markdown_to_html("# T\n\n- a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n",
                            "T")
    assert "<table>" in html and "<li>" in html and "<h1>" in html
    assert _code("x") == "`x`"
    assert _truncate("abc", 2).endswith("…")


def test_pdf_paths_are_absolute(tmp_path, monkeypatch):
    """Chrome resolves --print-to-pdf against ITS working directory.

    A relative target silently writes elsewhere, or fails with
    "Failed to write file" while still exiting 0. This asserts the command is
    built with an absolute path, without needing a browser present.
    """
    from asset_factory import htmlreport

    (tmp_path / "sample.html").write_text("<h1>x</h1>", encoding="utf8")
    captured = {}

    class FakeResult:
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        target = next(c for c in cmd if c.startswith("--print-to-pdf="))
        path = pathlib.Path(target.split("=", 1)[1])
        captured["absolute"] = path.is_absolute()
        path.write_bytes(b"%PDF-1.4" + b"0" * 2048)
        return FakeResult()

    monkeypatch.setattr(htmlreport.subprocess if hasattr(htmlreport, "subprocess")
                        else __import__("subprocess"), "run", fake_run,
                        raising=False)
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)

    written = htmlreport.render_pdfs(tmp_path, browser="fake-browser")
    assert captured.get("absolute") is True, "--print-to-pdf path was relative"
    assert written and written[0].suffix == ".pdf"


def test_print_stylesheet_forces_light_palette():
    """A dark-themed PDF wastes ink and reads badly on paper."""
    from asset_factory.htmlreport import CSS

    assert "@media print" in CSS
    print_block = CSS.split("@media print", 1)[1]
    assert "--paper:#fff" in print_block
    assert "page-break-inside:avoid" in print_block
