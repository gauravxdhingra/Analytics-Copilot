from pathlib import Path

from copilot_devtools.adr_index import check

README_HEAD = "# ADRs\n\n| # | Title | Status | Ref |\n|---|---|---|---|\n"


def _adr(directory: Path, name: str, status: str) -> None:
    (directory / name).write_text(f"# x\n\n- **Status:** {status}\n- **Date:** 2026-01-01\n")


def _readme(directory: Path, rows: list[str]) -> None:
    (directory / "README.md").write_text(README_HEAD + "\n".join(rows) + "\n")


def test_consistent_index_passes(tmp_path: Path) -> None:
    _adr(tmp_path, "0000-template.md", "Proposed | Accepted")
    _adr(tmp_path, "0001-a.md", "Accepted")
    _readme(tmp_path, ["| [0001](0001-a.md) | A | Accepted | D1 |"])
    assert check(tmp_path) == []


def test_missing_row_is_reported(tmp_path: Path) -> None:
    _adr(tmp_path, "0001-a.md", "Accepted")
    _readme(tmp_path, [])
    assert any("0001-a.md" in e and "not listed" in e for e in check(tmp_path))


def test_status_mismatch_is_reported(tmp_path: Path) -> None:
    _adr(tmp_path, "0001-a.md", "Accepted")
    _readme(tmp_path, ["| [0001](0001-a.md) | A | Proposed | D1 |"])
    assert any("status" in e for e in check(tmp_path))


def test_row_for_missing_file_is_reported(tmp_path: Path) -> None:
    _readme(tmp_path, ["| [0009](0009-gone.md) | Gone | Accepted | - |"])
    assert any("0009-gone.md" in e and "no such file" in e for e in check(tmp_path))


def test_duplicate_numbers_are_reported(tmp_path: Path) -> None:
    _adr(tmp_path, "0001-a.md", "Accepted")
    _adr(tmp_path, "0001-b.md", "Accepted")
    _readme(
        tmp_path,
        ["| [0001](0001-a.md) | A | Accepted | D1 |", "| [0001](0001-b.md) | B | Accepted | D1 |"],
    )
    assert any("duplicate" in e for e in check(tmp_path))
