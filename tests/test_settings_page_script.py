"""The settings page's own script (it cannot run in pytest, so its known traps are checked as text)."""

from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "app" / "templates" / "settings" / "index.html"


def test_the_settings_script_works_without_a_hash():
    # 21 Nov 2026: "location.hash && …" gave "" without a #part, "".closest() threw, and no list on the page saved
    script = PAGE.read_text(encoding="utf-8")
    assert "location.hash &&" not in script and "location.hash.length > 1 ?" in script
    assert "select.auto-save, input.auto-save" in script
