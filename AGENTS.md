# AGENTS.md

The instructions for coding agents in this repository live in **one** file:
[`CLAUDE.md`](CLAUDE.md). Read it first, in full — it carries the mandatory
pre-flight, the hard rules (ruff, pytest, TDD, no `except: pass`), the
architecture and the project knowledge.

This file is a pointer on purpose. A copy drifts from the original, and the
copy is always the one that gets read last.

Agent-specific wiring (not rules, just plumbing):

| Agent | Reads |
|---|---|
| Claude Code | `.claude/settings.json`, `.claude/skills/` |
| Codex | `.codex/hooks.json`, `.agents/skills/` |
