#!/usr/bin/env bash
# PreToolUse(Bash) hook: block `git commit` when working tree fails checks.
#
# Exit 0  = allow tool.
# Exit 2  = block tool, stderr is shown to Claude.
# Other  = non-blocking error (we still allow the tool).
#
# Reads JSON payload from stdin (Claude Code convention).
set -u

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "${CLAUDE_PROJECT_DIR:-}")"
[ -z "$REPO_ROOT" ] && { echo "[pre-commit-check] cannot determine repo root" >&2; exit 0; }

# Detect venv (./venv or ./.venv)
PYTHON=""
PYTEST=""
for candidate in "$REPO_ROOT/venv/bin" "$REPO_ROOT/.venv/bin"; do
  if [ -x "$candidate/python3" ]; then
    PYTHON="$candidate/python3"
    [ -x "$candidate/pytest" ] && PYTEST="$candidate/pytest"
    break
  fi
done
[ -z "$PYTHON" ] && PYTHON="$(command -v python3 || true)"

RUFF="$(command -v ruff || true)"
[ -z "$RUFF" ] && [ -x "$REPO_ROOT/venv/bin/ruff" ] && RUFF="$REPO_ROOT/venv/bin/ruff"
[ -z "$RUFF" ] && [ -x "$REPO_ROOT/.venv/bin/ruff" ] && RUFF="$REPO_ROOT/.venv/bin/ruff"

# Read payload
payload="$(cat)"
cmd="$(printf '%s' "$payload" | "$PYTHON" -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print((d.get("tool_input") or {}).get("command", ""))
except Exception:
    print("")
' 2>/dev/null)"

# Only act on `git commit`
case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

# Bypass
if [ "${ALLOW_DIRTY_COMMIT:-0}" = "1" ]; then
  echo "[pre-commit-check] ALLOW_DIRTY_COMMIT=1, skipping checks" >&2
  exit 0
fi

cd "$REPO_ROOT" || { echo "[pre-commit-check] cannot cd $REPO_ROOT" >&2; exit 2; }

fail() {
  echo "" >&2
  echo "============================================================" >&2
  echo "  COMMIT BLOCKED by .claude pre-commit-check" >&2
  echo "============================================================" >&2
  echo "$1" >&2
  echo "" >&2
  echo "Fix the issues above, then re-run the commit." >&2
  echo "Override (NOT recommended): ALLOW_DIRTY_COMMIT=1 git commit ..." >&2
  exit 2
}

# 1. Forbidden patterns in staged diff
diff="$(git diff --cached --unified=0 -- '*.py' 2>/dev/null || true)"
if printf '%s' "$diff" | grep -E '^\+[[:space:]]*except([[:space:]]+Exception)?[[:space:]]*:[[:space:]]*pass[[:space:]]*$' >/dev/null; then
  fail "Found forbidden \`except: pass\` / \`except Exception: pass\` in staged diff.
See doc/code_guidelines.md section 2.1. Log the error, re-raise, or return a meaningful error."
fi

# 2. ruff
if [ -n "$RUFF" ]; then
  if ! "$RUFF" check . >/tmp/ruff-precommit.log 2>&1; then
    fail "ruff check failed:
$(cat /tmp/ruff-precommit.log)"
  fi
else
  echo "[pre-commit-check] WARN: ruff not installed (pip install ruff)" >&2
fi

# 3. pytest
if [ -n "$PYTEST" ] && [ -x "$PYTEST" ]; then
  if ! "$PYTEST" -q tests/ >/tmp/pytest-precommit.log 2>&1; then
    fail "pytest failed:
$(tail -50 /tmp/pytest-precommit.log)"
  fi
else
  echo "[pre-commit-check] WARN: pytest not found in venv" >&2
fi

echo "[pre-commit-check] OK: ruff + pytest + diff scan passed" >&2
exit 0
