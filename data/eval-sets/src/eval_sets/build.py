"""Regenerate the committed gold-set files: `uv run build-gold-sets`."""

import sys

from copilot_evals.loader import dump_gold_set
from halcyon_world import generate_world

from eval_sets.insights_v0 import DATA_FILE, build


def main() -> int:
    dump_gold_set(build(generate_world(seed=7)), DATA_FILE)
    sys.stdout.write(f"wrote {DATA_FILE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
