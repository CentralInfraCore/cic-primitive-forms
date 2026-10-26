#!/bin/bash
# The verdict of one Go mutant's test run, for the go-mutesting executors (mutation_shard_exec.sh, mutation_diff_exec.sh,
# mutation_reduced_exec.sh), sourced by them (#94).
#   go_test_verdict PKG [go test flags...]   prints killed, survived, uncompilable or unknown
# `go test` exits 1 for a failing test AND for a package that does not build (`FAIL pkg [build failed]`), and it reports a vet failure
# the same way; go-mutesting type-checks only the original file. So a `[build failed]` (or `[setup failed]`) is decided by building
# without vet: it builds, so vet caught the mutant (killed, as before); it does not, so the mutant is uncompilable and counts as
# neither killed nor survived.
go_test_verdict() {
	local pkg=$1 out status
	shift
	out=$(go test "$@" "$pkg" 2>&1)
	status=$?
	case $status in
	0) echo survived ;;
	1)
		if grep -q -e '\[build failed\]' -e '\[setup failed\]' <<<"$out" && ! go test -vet=off -count=1 -run '^$' "$pkg" >/dev/null 2>&1; then
			echo uncompilable
		else
			echo killed
		fi
		;;
	2) echo uncompilable ;;
	*) echo unknown ;;
	esac
}
