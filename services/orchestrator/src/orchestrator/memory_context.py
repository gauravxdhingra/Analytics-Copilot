"""What the model is shown from memory: hints, wrapped as data, bounded, and unable to escape.

Memory is context, never authority. It reaches a model only inside an <untrusted source="memory">
block, so the model treats it as something to resolve references against ("the next 5", "in EUR"),
not as instructions. Code builds the text; closing tags inside it are escaped by the wrapper.
"""

import json
from collections.abc import Sequence

from copilot_memory import Preference, Turn
from policy_gateway.content import wrap_untrusted


def render_memory(
    history: Sequence[Turn],
    preferences: Sequence[Preference],
    *,
    max_turns: int = 5,
    max_prefs: int = 10,
    max_chars: int = 6000,
    max_plan_chars: int = 800,
) -> str:
    lines: list[str] = []
    turns = list(history)[-max_turns:]
    if turns:
        lines.append("Earlier in this conversation (oldest first):")
        for i, turn in enumerate(turns, 1):
            plan = json.dumps(turn.plan, sort_keys=True) if turn.plan else "none"
            if len(plan) > max_plan_chars:
                plan = plan[:max_plan_chars] + "…"
            lines += [
                f"{i}. Question: {turn.question}",
                f"   Plan that was run: {plan}",
                f"   Result: {turn.status}: {turn.summary}",
            ]
    saved = list(preferences)[:max_prefs]
    if saved:
        lines.append("Saved by the user:")
        for pref in saved:
            if pref.kind == "saved_definition":
                lines.append(f'- definition "{pref.name}": {pref.text}')
            else:
                lines.append(f"- preference: {pref.text}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + "…"
    return wrap_untrusted(body, "memory")
