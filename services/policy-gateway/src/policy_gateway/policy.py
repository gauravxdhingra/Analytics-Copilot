"""Policy as code: a versioned YAML file of who may call which tool, under which limits."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from policy_gateway.tools import TOOL_TIERS


class PolicyError(Exception):
    """The policy file is missing, malformed, or names something that does not exist."""


class _Strict(BaseModel):
    # Unknown keys are errors so a typo cannot silently widen access.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Constraints(_Strict):
    max_rows: int | None = None
    max_runtime_s: int | None = None
    require_purpose: bool = False


class ToolRule(_Strict):
    name: str
    constraints: Constraints = Constraints()
    requires_role: tuple[str, ...] = ()


class Defaults(_Strict):
    # The underlying system decides data scope (D6); the gateway never does.
    data_scope: Literal["inherit_user"] = "inherit_user"
    max_tool_calls_per_run: int
    max_cost_usd_per_run: Decimal
    redact: tuple[str, ...] = ()
    tainted_columns: tuple[str, ...] = ()
    tainted_sources: tuple[str, ...] = ()


class Policy(_Strict):
    version: int
    agents: dict[str, tuple[ToolRule, ...]]
    defaults: Defaults

    @classmethod
    def model_validate_policy(cls, raw: dict[str, Any]) -> "Policy":
        try:
            agents: dict[str, tuple[ToolRule, ...]] = {}
            for agent, body in dict(raw.get("agents", {})).items():
                rules = []
                for entry in dict(body).get("tools", []):
                    if isinstance(entry, str):
                        rules.append(ToolRule(name=entry))
                    else:
                        ((name, options),) = dict(entry).items()
                        rules.append(ToolRule.model_validate({"name": name, **(options or {})}))
                agents[str(agent)] = tuple(rules)
            policy = cls.model_validate({**raw, "agents": agents})
        except (ValidationError, ValueError, TypeError) as exc:
            raise PolicyError(f"invalid policy: {type(exc).__name__}") from None
        for agent, agent_rules in policy.agents.items():
            for rule in agent_rules:
                if rule.name not in TOOL_TIERS:
                    raise PolicyError(f"unknown tool {rule.name!r} for agent {agent!r}")
        return policy

    @classmethod
    def from_file(cls, path: Path) -> "Policy":
        try:
            raw = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            raise PolicyError(f"cannot read policy file {path.name}") from None
        if not isinstance(raw, dict):
            raise PolicyError("policy file must be a mapping")
        return cls.model_validate_policy(raw)

    def rule(self, agent: str, tool: str) -> ToolRule | None:
        return next((r for r in self.agents.get(agent, ()) if r.name == tool), None)

    def allowed_tools(self, agent: str) -> set[str]:
        return {r.name for r in self.agents.get(agent, ())}

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
