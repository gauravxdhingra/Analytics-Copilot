import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from copilot_catalogue.models import Glossary, Relationship, Store


class CatalogueError(Exception):
    """The catalogue files are malformed or inconsistent."""


class Catalogue:
    def __init__(self, stores: dict[str, Store], glossary: Glossary) -> None:
        self.stores = stores
        self.glossary = glossary
        self._check()
        canonical = json.dumps(
            {
                "stores": {k: v.model_dump(mode="json") for k, v in sorted(stores.items())},
                "glossary": glossary.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        # Versioned by content, so every trace can say which catalogue produced an answer.
        self.version = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def load(cls, directory: Path) -> "Catalogue":
        stores: dict[str, Store] = {}
        glossary = Glossary(terms={})
        try:
            for path in sorted(directory.glob("*.yaml")):
                raw = yaml.safe_load(path.read_text())
                if path.name == "glossary.yaml":
                    glossary = Glossary.model_validate(
                        {"terms": {k.lower(): v for k, v in raw["terms"].items()}}
                    )
                else:
                    store = Store.model_validate(raw)
                    stores[store.store] = store
        except (ValidationError, OSError, yaml.YAMLError, KeyError, TypeError):
            raise CatalogueError(f"invalid catalogue in {directory.name}") from None
        return cls(stores, glossary)

    def _check(self) -> None:
        for name, store in self.stores.items():
            columns = {c.name for c in store.columns}
            referenced = [
                *store.grain,
                *store.dates,
                *(f.column for f in store.required_filters),
                *(m.column for m in store.money),
                *(m.currency_column for m in store.money),
                *(p for m in store.money for p in m.pinned_by),
            ]
            missing = [c for c in referenced if c not in columns]
            if missing:
                raise CatalogueError(f"{name}: unknown columns {sorted(set(missing))}")
            for rel in store.relationships:
                target = self.stores.get(rel.to)
                if target is None:
                    raise CatalogueError(f"{name}: relationship to unknown store {rel.to}")
                for mine, theirs in rel.join_on:
                    if mine not in columns or target.column(theirs) is None:
                        raise CatalogueError(
                            f"{name}: relationship {rel.name} uses unknown columns"
                        )

    def store(self, name: str) -> Store:
        try:
            return self.stores[name]
        except KeyError:
            raise CatalogueError(f"unknown store {name}") from None

    def relationship(self, source: str, target: str) -> Relationship | None:
        return next((r for r in self.store(source).relationships if r.to == target), None)

    def untrusted_columns(self) -> set[str]:
        return {c.name for s in self.stores.values() for c in s.columns if c.untrusted}
