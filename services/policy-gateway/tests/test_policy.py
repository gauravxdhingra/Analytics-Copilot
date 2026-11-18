import pytest
import yaml
from policy_gateway.policy import Policy, PolicyError
from policy_gateway.testing import EXAMPLE_POLICY as POLICY


def test_the_spec_shaped_policy_loads(policy: Policy) -> None:
    assert policy.version == 3
    rule = policy.rule("insights.query_planner", "analytics.run_validated_select")
    assert rule is not None
    assert rule.constraints.max_rows == 5000 and rule.constraints.require_purpose
    assert rule.requires_role == ("analyst", "treasury_manager")
    assert policy.rule("insights.query_planner", "analytics.describe_store") is not None


def test_extraction_agents_get_no_tools(policy: Policy) -> None:
    assert policy.allowed_tools("recon.remittance_extractor") == set()


def test_an_unknown_agent_has_no_tools(policy: Policy) -> None:
    assert policy.allowed_tools("someone.else") == set()
    assert policy.rule("someone.else", "analytics.describe_store") is None


def test_a_tool_that_does_not_exist_in_the_registry_is_a_load_error() -> None:
    bad = {**POLICY, "agents": {"x": {"tools": ["analytics.drop_everything"]}}}
    with pytest.raises(PolicyError, match="unknown tool"):
        Policy.model_validate_policy(bad)


def test_there_is_no_tier_above_draft() -> None:
    from policy_gateway.tools import TOOL_TIERS, Tier

    assert {t.value for t in Tier} == {"READ", "ANALYZE", "DRAFT"}
    assert all(isinstance(tier, Tier) for tier in TOOL_TIERS.values())
    assert not any(
        "approve" in name or "execute" in name or "release" in name for name in TOOL_TIERS
    )


def test_unknown_keys_are_rejected_so_a_typo_cannot_widen_access() -> None:
    bad = {**POLICY, "defaults": {**POLICY["defaults"], "max_tool_calls": 99}}
    with pytest.raises(PolicyError):
        Policy.model_validate_policy(bad)


def test_a_missing_version_is_rejected() -> None:
    without = {k: v for k, v in POLICY.items() if k != "version"}
    with pytest.raises(PolicyError):
        Policy.model_validate_policy(without)


def test_the_policy_loads_from_yaml_and_a_missing_file_fails_closed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(POLICY))
    assert Policy.from_file(path).version == 3
    with pytest.raises(PolicyError):
        Policy.from_file(tmp_path / "absent.yaml")


def test_the_policy_has_a_stable_hash_for_ledger_and_traces(policy: Policy) -> None:
    assert policy.digest == Policy.model_validate_policy(POLICY).digest
    changed = Policy.model_validate_policy({**POLICY, "version": 4})
    assert changed.digest != policy.digest
