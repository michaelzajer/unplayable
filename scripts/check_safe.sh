#!/usr/bin/env bash
# Run this before every push. It fails (non-zero) if anything risky is about to go to GitHub.
# Usage:  bash scripts/check_safe.sh
set -u
fail=0

echo "1. .env ignored by git?"
if git check-ignore .env >/dev/null 2>&1; then
  echo "   PASS"
else
  echo "   FAIL — .env is not gitignored. Do not push."; fail=1
fi

echo "2. no real .env tracked?"
if git ls-files | grep -qE '(^|/)\.env$'; then
  echo "   FAIL — a real .env is tracked. Run: git rm --cached .env"; fail=1
else
  echo "   PASS (.env.example is fine)"
fi

echo "3. data/ not tracked?"
if git ls-files | grep -qE '^data/'; then
  echo "   FAIL — files under data/ are tracked. Run: git rm -r --cached data"; fail=1
else
  echo "   PASS"
fi

echo "4. no real API key in tracked files?"
# Real Anthropic keys start sk-ant-api03-. Placeholders in docs do not, so they are ignored.
if git grep -lI "sk-ant-api03-" -- . ':(exclude).env.example' ':(exclude)scripts/check_safe.sh' >/dev/null 2>&1; then
  echo "   FAIL — a real key string is in a tracked file:"
  git grep -lI "sk-ant-api03-" -- . ':(exclude).env.example' ':(exclude)scripts/check_safe.sh' | sed 's/^/      /'
  fail=1
else
  echo "   PASS"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "All clear. Safe to push."
else
  echo "STOP. Fix the failures above before pushing."
fi
exit "$fail"
