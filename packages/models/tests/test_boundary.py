"""The provider SDK may be imported in exactly one place (ADR 0002)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ALLOWED = ROOT / "packages" / "models" / "src" / "copilot_models" / "adapters" / "gemini.py"
ADAPTER_TEST_DOUBLE = (
    ROOT / "packages" / "models" / "src" / "copilot_models" / "adapters" / "gemini_stub.py"
)
TESTS_MAY_BUILD_SDK_OBJECTS = {"test_gemini.py"}
SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__", ".mypy_cache", ".ruff_cache"}


def _python_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def _imports_provider(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "google" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "google":
            return True
    return False


def test_only_the_gemini_adapter_imports_the_provider_sdk() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in _python_files()
        if p not in {ALLOWED, ADAPTER_TEST_DOUBLE}
        and p.name not in TESTS_MAY_BUILD_SDK_OBJECTS
        and _imports_provider(p)
    ]
    assert offenders == []


def test_the_adapter_itself_does_import_it() -> None:
    assert _imports_provider(ALLOWED)
