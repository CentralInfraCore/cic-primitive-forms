package genprom

import "testing"

func TestG(t *testing.T) {
	if got := Use(); got != "a" {
		t.Fatalf("got %q, want %q", got, "a")
	}
}
