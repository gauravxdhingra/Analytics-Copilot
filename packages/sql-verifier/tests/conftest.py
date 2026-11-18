from pathlib import Path

import pytest
from copilot_catalogue import Catalogue
from copilot_sql_verifier import Verifier

CATALOGUE_DIR = Path(__file__).resolve().parents[3] / "data" / "catalogue"


@pytest.fixture(scope="session")
def catalogue() -> Catalogue:
    return Catalogue.load(CATALOGUE_DIR)


@pytest.fixture(scope="session")
def verifier(catalogue: Catalogue) -> Verifier:
    return Verifier(catalogue)
