import asyncio

from copilot_evals.agents import AgentAnswer, EvalAgent
from copilot_evals.models import GoldSet
from copilot_evals.report import EvalReport, build_report
from copilot_evals.scoring import ItemResult, score_item

ITEM_TIMEOUT_SECONDS = 120.0  # an agent that hangs fails its item; it never blocks the run


async def run_agent(
    gold: GoldSet, agent: EvalAgent, *, seed: int = 0
) -> tuple[EvalReport, list[ItemResult], list[AgentAnswer]]:
    answers: list[AgentAnswer] = []
    for item in gold.items:
        try:
            answers.append(await asyncio.wait_for(agent.answer(item), ITEM_TIMEOUT_SECONDS))
        except TimeoutError:
            answers.append(AgentAnswer(error_code="TIMEOUT"))
    results = [score_item(item, a) for item, a in zip(gold.items, answers, strict=True)]
    report = build_report(gold, results, answers, agent=agent.name, seed=seed)
    return report, results, answers
