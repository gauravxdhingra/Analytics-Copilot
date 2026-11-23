"""Run the eval harness: `uv run run-evals calibrate` or `uv run run-evals baseline`."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from copilot_auth.testing import keycloak_token_for
from copilot_evals.agents import EvalAgent
from copilot_evals.gates import GateResult, Gates, evaluate_gates
from copilot_evals.loader import load_gold_set
from copilot_evals.report import EvalReport
from copilot_evals.runner import run_agent
from copilot_models import ModelClient, RecordingClient, ReplayClient
from copilot_models.selection import select_live_model
from query_api.app import create_app
from query_api.config import Settings

from eval_sets.agents import (
    CarelessAgent,
    LlmNaiveAgent,
    OracleAgent,
    OrchestratorAgent,
    QueryApiExecutor,
)
from eval_sets.insights_v0 import DATA_FILE

DATA_DIR = DATA_FILE.parent
GATES_FILE = DATA_DIR / "gates.yaml"
CASSETTE = DATA_DIR / "cassettes" / "naive_baseline_v0.json"
AGENT_CASSETTE = DATA_DIR / "cassettes" / "copilot_v0.json"
ACCEPTED_DIR = DATA_DIR / "accepted"  # the last report a human accepted: the regression baseline


def previous_execution_accuracy(agent: str, directory: Path = ACCEPTED_DIR) -> float | None:
    """Execution accuracy in the last accepted report for this agent, or None if there is none."""
    path = directory / f"{agent}.json"
    try:
        found = json.loads(path.read_text())["metrics"]["execution_accuracy"]
        return float(found["successes"]) / float(found["total"])
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def _settings() -> Settings:
    issuer = f"{os.environ.get('KEYCLOAK_URL', 'http://localhost:8081')}/realms/halcyon"
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    password = os.environ["QUERY_DB_PASSWORD"]
    return Settings(
        issuer=issuer,
        jwks_url=f"{issuer}/protocol/openid-connect/certs",
        dsn=f"postgresql://copilot_query:{password}@{host}/analytics",
    )


def _model(mode: str, cassette: Path = CASSETTE, command: str = "baseline") -> ModelClient:
    if mode == "replay":
        if not cassette.exists():
            raise SystemExit(
                f"No recording at {cassette}. Record one with a key: "
                f"GROQ_API_KEY=... (or GEMINI_API_KEY) uv run run-evals {command} --mode record"
            )
        return ReplayClient(cassette)
    live = select_live_model(os.environ)
    if live is None:
        raise SystemExit("No model key: set GROQ_API_KEY or GEMINI_API_KEY (and MODEL_PROVIDER).")
    if mode == "record":
        cassette.parent.mkdir(parents=True, exist_ok=True)
        return RecordingClient(live, cassette)
    return live


def _print(report: EvalReport, gates: list[GateResult]) -> None:
    sys.stdout.write(report.to_markdown())
    for g in gates:
        mark = "PASS" if g.passed else "FAIL"
        note = "" if g.applicable else " (not applicable)"
        sys.stdout.write(f"  [{mark}] {g.name}: {g.observed} (needs {g.threshold}){note}\n")


async def _evaluate(agent_factory: Any, out: Path | None) -> tuple[EvalReport, list[GateResult]]:
    gold = load_gold_set(DATA_FILE)
    app = create_app(_settings())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://query-api") as http:
            executor = QueryApiExecutor(http, keycloak_token_for)
            agent: EvalAgent = agent_factory(executor)
            report, _, _ = await run_agent(gold, agent, seed=1)
    gates = evaluate_gates(report, Gates.from_yaml(GATES_FILE))
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{report.agent}.json").write_text(report.to_json())
        (out / f"{report.agent}.md").write_text(report.to_markdown())
    return report, gates


async def _calibrate(out: Path | None) -> int:
    """The oracle must clear every gate and the careless agent must fail the safety gates."""
    oracle, oracle_gates = await _evaluate(OracleAgent, out)
    careless, careless_gates = await _evaluate(CarelessAgent, out)
    _print(oracle, oracle_gates)
    _print(careless, careless_gates)
    failed_by_careless = {g.name for g in careless_gates if not g.passed}
    ok = all(g.passed for g in oracle_gates) and {"trap_safety", "silent_wrong_rate"} <= (
        failed_by_careless
    )
    sys.stdout.write(
        "\nCalibration: "
        + ("OK, the gates pass the ceiling and fail the floor.\n" if ok else "FAILED\n")
    )
    return 0 if ok else 1


async def _baseline(mode: str, out: Path | None, enforce: bool) -> int:
    model = _model(mode)
    report, gates = await _evaluate(lambda ex: LlmNaiveAgent(model, ex), out)
    _print(report, gates)
    return 1 if enforce and not all(g.passed for g in gates) else 0


async def _copilot(mode: str, out: Path | None, enforce: bool) -> int:
    """Evaluate the product agent: the full orchestrator through the real stack."""
    from copilot_auth import JwksKeyProvider, TokenValidator
    from copilot_auth.testing import keycloak_token_for
    from orchestrator import InsightsOrchestrator
    from orchestrator.stack import local_stack
    from policy_gateway import RunContext
    from policy_gateway.postgres_ledger import PostgresLedger, ensure_schema

    model = _model(mode, AGENT_CASSETTE, "copilot")
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    ensure_schema(
        f"postgresql://copilot_owner:{os.environ['POSTGRES_OWNER_PASSWORD']}@{host}/analytics",
        app_password=os.environ["GATEWAY_DB_PASSWORD"],
        reset=True,
    )
    ledger = PostgresLedger(
        f"postgresql://copilot_gateway:{os.environ['GATEWAY_DB_PASSWORD']}@{host}/analytics"
    )
    issuer = f"{os.environ.get('KEYCLOAK_URL', 'http://localhost:8081')}/realms/halcyon"
    validator = TokenValidator(
        issuer=issuer,
        audience="mcp-analytics",
        keys=JwksKeyProvider(f"{issuer}/protocol/openid-connect/certs"),
    )
    gold = load_gold_set(DATA_FILE)
    async with local_stack(ledger) as stack:
        orchestrator = InsightsOrchestrator(
            model, stack.gateway, stack.catalogue, versions={"policy": str(stack.policy.version)}
        )

        async def context_for(persona: str) -> RunContext:
            token = await keycloak_token_for(persona, audience="mcp-analytics")
            return RunContext(
                run_id=f"eval-{persona}",
                trace_id="eval",
                principal=validator.validate(token),
                upstream_tokens={"analytics": token},
            )

        agent = OrchestratorAgent(orchestrator, context_for)
        report, _, _ = await run_agent(gold, agent, seed=1)
    gates = evaluate_gates(
        report,
        Gates.from_yaml(GATES_FILE),
        previous_execution_accuracy=previous_execution_accuracy("copilot"),
    )
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "copilot.json").write_text(report.to_json())
        (out / "copilot.md").write_text(report.to_markdown())
    _print(report, gates)
    return 1 if enforce and not all(g.passed for g in gates) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-evals")
    sub = parser.add_subparsers(dest="command", required=True)
    calibrate = sub.add_parser(
        "calibrate", help="check that the gates pass a ceiling, fail a floor"
    )
    calibrate.add_argument("--out", type=Path)
    baseline = sub.add_parser("baseline", help="evaluate the naive LLM text-to-SQL baseline")
    baseline.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    baseline.add_argument("--out", type=Path)
    baseline.add_argument("--enforce", action="store_true")
    copilot = sub.add_parser("copilot", help="evaluate the product agent against the release gates")
    copilot.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    copilot.add_argument("--out", type=Path)
    copilot.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        # psycopg's async mode cannot run on the default Windows (Proactor) event loop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if args.command == "copilot":
        return asyncio.run(_copilot(args.mode, args.out, args.enforce))
    if args.command == "calibrate":
        return asyncio.run(_calibrate(args.out))
    return asyncio.run(_baseline(args.mode, args.out, args.enforce))


if __name__ == "__main__":
    sys.exit(main())
