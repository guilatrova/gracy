#!/bin/sh
# Sanity test for the standalone `gracy` binary. Pure shell, no Python required
# (that's the whole point: it proves the binary stands on its own).
#
#   packaging/smoke-test.sh [path-to-binary]   # default: gracy on PATH
#
# Env:
#   GRACY_TEST_BASE   base URL for the live network check (default: PokeAPI).
#                     Set to skip=1 semantics by exporting GRACY_SKIP_NET=1.
set -eu

BIN="${1:-gracy}"
BASE="${GRACY_TEST_BASE:-https://pokeapi.co/api/v2}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }
have() { command -v "$1" >/dev/null 2>&1; }

echo "gracy sanity test"
echo "binary: $BIN"
"$BIN" --version >/dev/null 2>&1 || { echo "cannot run $BIN" >&2; exit 2; }
echo "version: $("$BIN" --version)"
echo

# ------------------------------------------------------------------ offline: the binary stands alone
echo "[1/6] binary basics (offline)"
case "$("$BIN" --version 2>&1)" in
  gracy\ *) ok "--version prints a version" ;;
  *) bad "--version output unexpected" ;;
esac
"$BIN" --help          >/dev/null 2>&1 && ok "--help runs"        || bad "--help failed"
"$BIN" docs --help     >/dev/null 2>&1 && ok "docs --help runs"   || bad "docs --help failed"
"$BIN" monitor --help  >/dev/null 2>&1 && ok "monitor --help runs" || bad "monitor --help failed"

echo "[2/6] error handling"
if "$BIN" bogus-command >/dev/null 2>&1; then bad "unknown command should exit non-zero"; else ok "unknown command exits non-zero"; fi
if "$BIN" x 'not a valid command' --session "$TMP/e.json" --json >/dev/null 2>&1; then
  bad "bad explorer command should exit non-zero"
else
  ok "bad explorer command exits non-zero (parse error)"
fi

# ------------------------------------------------------------------ network: the Rust transport works
if [ "${GRACY_SKIP_NET:-0}" = "1" ]; then
  echo "[3/6] network checks SKIPPED (GRACY_SKIP_NET=1)"
else
  echo "[3/6] live request through the Rust transport ($BASE)"
  out="$("$BIN" x 'get /pokemon/ditto' --base "$BASE" --session "$TMP/n.json" --json 2>&1 || true)"
  case "$out" in
    *'"status": 200'*) ok "GET returned 200 (reqwest transport works)" ;;
    *) bad "live GET did not return 200 (network? output: $(echo "$out" | head -c 120))" ;;
  esac

  echo "[4/6] --stdio JSONL loop"
  stdio_out="$(printf '{"cmd": "get /pokemon/pikachu"}\n{"cmd": "endpoint get_pokemon"}\n' \
    | "$BIN" explore --stdio --base "$BASE" --session "$TMP/s.json" 2>&1 || true)"
  case "$stdio_out" in
    *'"status": 200'*) ok "stdio emits a JSON result line" ;;
    *) bad "stdio did not emit a 200 line" ;;
  esac
  case "$stdio_out" in
    *'"endpoint": "get_pokemon"'*) ok "stdio named an endpoint (session in memory)" ;;
    *) bad "stdio did not name the endpoint" ;;
  esac
  [ -f "$TMP/s.json" ] && ok "session file persisted" || bad "session file missing"

  echo "[5/6] save -> typed client + tests"
  "$BIN" x 'endpoint get_pokemon' --base "$BASE" --session "$TMP/s.json" >/dev/null 2>&1 || true
  "$BIN" x "save $TMP/out.py --tests" --base "$BASE" --session "$TMP/s.json" >/dev/null 2>&1 || true
  if [ -f "$TMP/out.py" ] && grep -q "class .*Gracy" "$TMP/out.py"; then
    ok "generated a typed Gracy client"
  else
    bad "did not generate a client module"
  fi
  [ -f "$TMP/out.cassette.db" ] && ok "generated a replay cassette" || bad "no cassette generated"

  echo "[6/6] --check drift detection (exit code contract)"
  if "$BIN" explore --check --base "$BASE" --session "$TMP/s.json" >/dev/null 2>&1; then
    ok "check exits 0 when the live shape matches"
  else
    bad "check should exit 0 against the same live API it recorded"
  fi
fi

echo
echo "passed: $pass   failed: $fail"
[ "$fail" -eq 0 ] || exit 1
echo "SANITY OK"
