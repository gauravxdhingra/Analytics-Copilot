from pathlib import Path

import yaml

from copilot_evals.models import GoldSet


def load_gold_set(path: Path) -> GoldSet:
    return GoldSet.model_validate(yaml.safe_load(path.read_text()))


def dump_gold_set(gold: GoldSet, path: Path) -> None:
    data = gold.model_dump(mode="json", exclude_defaults=False)
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=100, allow_unicode=True))
