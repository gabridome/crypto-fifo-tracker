#!/usr/bin/env bash
# Idempotent setup for the enforcement layer.
# Run from repo root or anywhere — uses git to find the root.
set -eu

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "Not in a git repository" >&2; exit 1; }
cd "$REPO_ROOT"

PLATFORM="$(uname -s)"
HOOK_SRC="$REPO_ROOT/scripts/hooks"
GIT_HOOK_SRC="$REPO_ROOT/scripts/git-hooks/pre-commit"
GIT_HOOK_DST="$REPO_ROOT/.git/hooks/pre-commit"
SETTINGS="$REPO_ROOT/.claude/settings.json"

# Detect venv
VENV_DIR=""
for c in "$REPO_ROOT/venv" "$REPO_ROOT/.venv"; do
  [ -d "$c" ] && { VENV_DIR="$c"; break; }
done

step() { echo ""; echo "▶ $1"; }
ok()   { echo "  ✓ $1"; }
warn() { echo "  ⚠ $1"; }
err()  { echo "  ✗ $1" >&2; }

step "1/6 — install ruff"
if [ -n "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/ruff" ]; then
  ok "ruff present in $VENV_DIR/bin"
elif command -v ruff >/dev/null 2>&1; then
  ok "ruff on PATH at $(command -v ruff)"
elif [ -n "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/pip" ]; then
  "$VENV_DIR/bin/pip" install ruff
  ok "ruff installed in $VENV_DIR/bin"
else
  warn "no venv detected — install ruff manually: pip install ruff"
fi

step "2/6 — chmod hook scripts"
chmod +x "$HOOK_SRC/skill-reminder.py" "$HOOK_SRC/pre-commit-check.sh" "$GIT_HOOK_SRC"
ok "chmod +x done"

step "3/6 — install .git/hooks/pre-commit"
if [ -e "$GIT_HOOK_DST" ] && [ ! -L "$GIT_HOOK_DST" ]; then
  cp -p "$GIT_HOOK_DST" "$GIT_HOOK_DST.bak.$(date +%s)"
  ok "existing hook backed up"
fi
cp -p "$GIT_HOOK_SRC" "$GIT_HOOK_DST"
chmod +x "$GIT_HOOK_DST"
ok "$GIT_HOOK_DST installed"

step "4/6 — verify .claude/settings.json hooks"
if [ -f "$SETTINGS" ]; then
  python3 - "$SETTINGS" <<'PY' && ok "hooks block present" || err "hooks block missing"
import json, sys
with open(sys.argv[1]) as f:
    s = json.load(f)
hooks = s.get("hooks") or {}
need = ("UserPromptSubmit", "PreToolUse")
missing = [h for h in need if h not in hooks]
if missing:
    print(f"missing: {missing}", file=sys.stderr)
    sys.exit(1)
PY
else
  warn "$SETTINGS not found"
fi

step "5/6 — install user-level skill symlink (optional, for owner only)"
USER_SKILLS="$HOME/.claude/skills"
SKILL_SRC="$REPO_ROOT/.claude/skills/before-coding"
if [ -d "$SKILL_SRC" ]; then
  mkdir -p "$USER_SKILLS"
  if [ -L "$USER_SKILLS/before-coding" ] || [ ! -e "$USER_SKILLS/before-coding" ]; then
    ln -sfn "$SKILL_SRC" "$USER_SKILLS/before-coding"
    ok "symlink: $USER_SKILLS/before-coding -> $SKILL_SRC"
  else
    warn "$USER_SKILLS/before-coding exists and is not a symlink — leaving alone"
  fi
fi

step "6/6 — smoke test git hook on benign diff"
TMPF="$REPO_ROOT/.enforcement-smoke.$$"
echo "# smoke test" > "$TMPF"
git add -- "$TMPF" 2>/dev/null || true
if "$GIT_HOOK_DST" >/dev/null 2>&1; then
  ok "git hook ran cleanly on benign diff"
else
  warn "git hook reported issues on benign diff — check above"
fi
git reset -q -- "$TMPF" 2>/dev/null || true
rm -f "$TMPF"

echo ""
echo "✅ Setup complete."
echo ""
echo "Test the blocker:"
echo "  printf 'try:\\n    pass\\nexcept: pass\\n' > /tmp/bad.py"
echo "  cp /tmp/bad.py $REPO_ROOT/bad.py && git -C $REPO_ROOT add bad.py"
echo "  git -C $REPO_ROOT commit -m 'should fail'   # expect block"
echo "  git -C $REPO_ROOT reset HEAD bad.py && rm $REPO_ROOT/bad.py"
