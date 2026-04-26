#!/usr/bin/env python3
"""UserPromptSubmit hook: inject skill reminders for task-trigger keywords.

Claude Code passes the hook payload as JSON on stdin. We read the user prompt,
detect intent, and print a JSON object on stdout with `hookSpecificOutput.
additionalContext` — Claude Code injects that string into the conversation as
if it were a system note.

Exit code 0 = continue; never block (UserPromptSubmit must be unobtrusive).
"""
from __future__ import annotations

import json
import re
import sys

TASK_TRIGGERS = re.compile(
    r"\b("
    r"implement|implementa|implementare|"
    r"refactor|refattorizza|"
    r"fix|risolvi|sistema|"
    r"build|costruisci|"
    r"add|aggiungi|"
    r"create|crea|"
    r"write|scrivi|"
    r"change|modifica|cambia"
    r")\b",
    re.IGNORECASE,
)

BUG_TRIGGERS = re.compile(
    r"\b(bug|errore|crash|fail|broken|non funziona|non va)\b",
    re.IGNORECASE,
)

PLAN_TRIGGERS = re.compile(
    r"\b(plan|piano|pianifica|design|progetta|brainstorm)\b",
    re.IGNORECASE,
)

DONE_TRIGGERS = re.compile(
    r"\b(fatto|done|finito|completato|pronto|finished|complete)\b",
    re.IGNORECASE,
)

REMINDER_HEADER = (
    "Project enforcement reminder (crypto-fifo-tracker):\n"
    "Before any code change, follow `.claude/skills/before-coding/SKILL.md`:\n"
    "  1. Read doc/code_guidelines.md (or confirm already read this session)\n"
    "  2. Check doc/TODO.md for related items\n"
    "  3. Propose a feature branch (git checkout -b feature/...)\n"
    "  4. Write failing test FIRST (TDD red-green-refactor)\n"
    "  5. Run `ruff check .` and `pytest tests/` before committing\n"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return 0

    extras: list[str] = []

    if TASK_TRIGGERS.search(prompt):
        extras.append(REMINDER_HEADER)
        extras.append(
            "-> Invoke `superpowers:brainstorming` if this introduces new design,\n"
            "   then `superpowers:writing-plans`, then `superpowers:test-driven-development`."
        )

    if BUG_TRIGGERS.search(prompt):
        extras.append(
            "-> Bug detected: invoke `superpowers:systematic-debugging` BEFORE proposing\n"
            "   any fix. Reproduce first, root-cause second, fix third."
        )

    if PLAN_TRIGGERS.search(prompt):
        extras.append("-> Planning detected: invoke `superpowers:writing-plans`.")

    if DONE_TRIGGERS.search(prompt):
        extras.append(
            "-> Completion language detected: BEFORE agreeing, invoke\n"
            "   `superpowers:verification-before-completion`. No claim without\n"
            "   fresh `pytest` + `ruff check` output in the SAME message."
        )

    if not extras:
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(extras),
        }
    }
    json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
