package main

import (
	"strings"
	"testing"
)

func TestPathCaseIDs(t *testing.T) {
	if err := checkPathCaseIDs(pathCases); err != nil {
		t.Fatalf("the whole table: %v", err)
	}
	repeated := append([]string{}, pathCases...)
	repeated[1] = repeated[0]
	invented := append(append([]string{}, pathCases...), "invented")
	for _, c := range []struct {
		name string
		ids  []string
		want string
	}{
		{"empty", nil, "is missing"},
		{"one missing", pathCases[1:], "the path case " + pathCases[0] + " is missing"},
		{"one repeated in place of another", repeated, "the path case " + pathCases[0] + " is repeated"},
		{"one the harness does not know", invented, "is not one the harness knows"},
	} {
		err := checkPathCaseIDs(c.ids)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: got %v, want %q", c.name, err, c.want)
		}
	}
}
