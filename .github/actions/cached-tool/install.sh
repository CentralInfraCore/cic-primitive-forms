#!/bin/bash
# Install one pinned tool unless a cached binary is there AND passes its check. Called by action.yml; tested by
# tools/test_cached_tool.py with shims for cargo and go. Environment: KIND (cargo|go), NAME, SPEC, BIN, HIT (the cache-hit output).
set -euo pipefail
: "${KIND:?}" "${NAME:?}" "${SPEC:?}" "${BIN:?}"
HIT=${HIT:-}

# The check of a restored binary. It has to be the tool that SPEC names and has to start; a file that is merely executable
# (truncated, another tool, another version) is not accepted.
works() {
  [ -x "$BIN" ] || return 1
  case "$KIND" in
    cargo)
      # run the way cargo runs a subcommand: `cargo mutants --help`, not `cargo-mutants --help`
      cargo +"${RUST_TOOLCHAIN:-stable}" "${NAME#cargo-}" --help >/dev/null 2>&1 ;;
    go)
      # the build information embedded in the binary names the main package and the module version it was built from
      local info rc
      info=$(go version -m "$BIN" 2>/dev/null) || return 1
      local want_path=${SPEC%@*} want_mod
      want_mod=${want_path%/cmd/*}
      grep -Eq "^[[:space:]]+path[[:space:]]+${want_path}\$" <<< "$info" || return 1
      grep -Eq "^[[:space:]]+mod[[:space:]]+${want_mod}[[:space:]]+${SPEC#*@}([[:space:]]|\$)" <<< "$info" || return 1
      # and it has to start: a Go tool that prints its usage and exits 1 is fine, one that cannot be executed (126, 127) or dies
      # on a signal (>128) is not
      rc=0; "$BIN" --help >/dev/null 2>&1 || rc=$?
      [ "$rc" -lt 126 ] ;;
    *) return 1 ;;
  esac
}

if [ "$HIT" = true ] && works; then
  echo "$NAME: from the cache"
  exit 0
fi
[ "$HIT" = true ] && echo "$NAME: the cached binary failed its check, building it again"
echo "$NAME: building $SPEC"
rm -f "$BIN"
if [ "$KIND" = cargo ]; then
  cargo +"${RUST_TOOLCHAIN:-stable}" install --locked "${SPEC%@*}" --version "${SPEC#*@}"
else
  go install "$SPEC"
fi
test -x "$BIN"
works || { echo "$NAME: the binary just built does not pass its own check" >&2; exit 1; }
