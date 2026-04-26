---
name: before-coding
description: Use BEFORE any non-trivial code change. Runs the project's mandatory pre-flight checklist (read guidelines, check TODO, propose branch, write tests first, run linters). Invoke whenever the user asks to implement, refactor, fix, build, add, create, write, or modify code.
---

# before-coding — Project pre-flight gate

This skill is the project entry gate. It MUST be invoked before any code change
beyond a trivial typo fix. It composes the universal `code_guidelines.md` rules
with superpowers skills into a single deterministic sequence.

## When to use

Invoke on the FIRST turn that involves any of:
- new feature, new file, new endpoint, new importer
- bug fix, regression, "non funziona"
- refactor, rename, extract, consolidate
- schema migration, DB write change, new column
- changes to `web/app.py`, `calculators/`, or `importers/`

Skip ONLY for:
- documentation typo fixes (no logic change)
- pure question answering with no code change

## The seven gates (run in order, no skipping)

### Gate 1 — Read the guidelines

If you have NOT already read these in this session, open them now:
- `doc/code_guidelines.md` (universal rules — 10 sections)
- `doc/project_guidelines.md` (project-specific: Decimal/EUR, importer pattern, port 5002)

State explicitly in your reply: "Guidelines read: yes/no, summary of binding
rules relevant to this task: ...". This anchors the rules in working memory.

### Gate 2 — Check the TODO

Read `doc/TODO.md`. If the user's request matches an existing TODO item, say
so. If it conflicts with priorities, raise it. If it is new, plan to add it
to TODO at the end of the task.

### Gate 3 — Brainstorm if new (superpowers:brainstorming)

If the change introduces NEW design (new feature, new abstraction, new
endpoint), invoke `superpowers:brainstorming` BEFORE writing code. Do not
skip on "small" features — small features accumulate.

### Gate 4 — Branch (code_guidelines.md §8.1)

Propose a branch name and run:

```bash
git checkout -b feature/<short-description>
```

Do not develop on `main` for non-trivial changes.

### Gate 5 — Test first (superpowers:test-driven-development)

Invoke `superpowers:test-driven-development` and follow red-green-refactor:
1. Write a failing test in `tests/` that pins the desired behaviour.
2. Run the test, observe it FAIL.
3. Implement the minimal change to make it pass.
4. Refactor under green.

For bug fixes: the failing test MUST reproduce the user-reported symptom
(code_guidelines.md §6.3).

### Gate 6 — Static analysis + tests (code_guidelines.md §8.2, §5.5)

Before claiming any work complete:

```bash
ruff check .
ruff format --check .
venv/bin/pytest tests/
```

Show the output in your reply. The pre-commit hook will block the commit
otherwise.

### Gate 7 — Verify (superpowers:verification-before-completion)

Invoke `superpowers:verification-before-completion` before saying "fatto",
"done", or proposing the commit. No completion claims without fresh verify
output in the SAME message.

## What success looks like

A reply that follows this skill always contains, in order:
1. Statement that guidelines are read
2. Reference to TODO.md item (if any)
3. Branch name proposal (or rationale for none)
4. Test code FIRST, then implementation
5. Captured output of `ruff check` and `pytest tests/`
6. Only then: completion claim and commit proposal

## Anti-patterns (from past retrospectives)

- "I'll run ruff later" → no, run it before claiming done
- "TDD doesn't apply for this small change" → it does, every time
- "Tests pass" without showing pytest output → forbidden
- "Should work" / "looks ok" → forbidden (code_guidelines.md §7)
- Forgetting `superpowers:requesting-code-review` before merging back to main
  → on completion, also run that skill for major features
