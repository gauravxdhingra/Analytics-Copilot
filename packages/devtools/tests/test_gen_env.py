"""`make up` must never fail on a missing secret, and must never change one that exists."""

import importlib.util
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "compose" / "gen_env.py"
spec = importlib.util.spec_from_file_location("gen_env", SCRIPT)
assert spec and spec.loader
gen_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_env)

EXAMPLE = "# template\nDB_PASSWORD=change-me\nMODE=demo\n"
COMPOSE = "x: ${DB_PASSWORD:?set it}\ny: ${CACHE_PASSWORD:?}\nz: ${OPTIONAL:-fallback}\n"


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    example, compose = tmp_path / ".env.example", tmp_path / "compose.yaml"
    example.write_text(EXAMPLE)
    compose.write_text(COMPOSE)
    return example, compose, tmp_path / ".env"


def _values(path: Path) -> dict[str, str]:
    pairs = (line.partition("=") for line in path.read_text().splitlines() if "=" in line)
    return {k: v for k, _, v in pairs if not k.startswith("#")}


def test_a_fresh_file_gets_random_secrets_and_keeps_plain_values(
    files: tuple[Path, Path, Path],
) -> None:
    example, compose, target = files
    gen_env.sync(example, compose, target)
    values = _values(target)
    assert values["MODE"] == "demo" and "change-me" not in target.read_text()
    assert len(values["DB_PASSWORD"]) >= 30
    assert stat.S_IMODE(target.stat().st_mode) == 0o600  # owner-only


def test_a_variable_compose_requires_but_the_template_lacks_is_still_generated(
    files: tuple[Path, Path, Path],
) -> None:
    example, compose, target = files
    gen_env.sync(example, compose, target)
    assert len(_values(target)["CACHE_PASSWORD"]) >= 30
    assert "OPTIONAL" not in _values(target)  # a defaulted variable is not required


def test_an_existing_file_keeps_every_value_and_only_gains_what_is_missing(
    files: tuple[Path, Path, Path],
) -> None:
    example, compose, target = files
    target.write_text("DB_PASSWORD=mine\nMODE=custom")  # note: no trailing newline
    target.chmod(0o600)
    added = gen_env.sync(example, compose, target)
    values = _values(target)
    assert values["DB_PASSWORD"] == "mine" and values["MODE"] == "custom"
    assert added == ["CACHE_PASSWORD"] and len(values["CACHE_PASSWORD"]) >= 30
    assert stat.S_IMODE(target.stat().st_mode) == 0o600  # permissions are not loosened


def test_running_it_again_changes_nothing(files: tuple[Path, Path, Path]) -> None:
    example, compose, target = files
    gen_env.sync(example, compose, target)
    before = target.read_text()
    assert gen_env.sync(example, compose, target) == []
    assert target.read_text() == before


def test_the_report_names_variables_and_never_shows_values(
    files: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    example, compose, target = files
    target.write_text("DB_PASSWORD=mine\n")
    gen_env.main(example, compose, target)
    out = capsys.readouterr().out
    assert "CACHE_PASSWORD" in out and "mine" not in out
    assert _values(target)["CACHE_PASSWORD"] not in out
