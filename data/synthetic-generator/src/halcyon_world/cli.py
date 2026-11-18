import argparse
import json
import sys
from pathlib import Path

from halcyon_world.generator import generate_world
from halcyon_world.load import load_world


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="halcyon-world")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("manifest", help="generate the world and write its manifest")
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument("--out", type=Path, default=Path("data/build/manifest.json"))
    load = sub.add_parser("load", help="generate the world and load it into Postgres")
    load.add_argument("--seed", type=int, default=7)
    load.add_argument("--dsn", required=True)
    args = parser.parse_args(argv)

    world = generate_world(seed=args.seed)
    if args.command == "manifest":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(world.manifest(), indent=2, sort_keys=True) + "\n")
        sys.stdout.write(f"wrote {args.out}\n")
    else:
        counts = load_world(world, args.dsn)
        sys.stdout.write(json.dumps(counts, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
