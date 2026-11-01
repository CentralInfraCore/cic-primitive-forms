package insrepro

import "testing"

func TestB(t *testing.T) {
	if UseB() != 2 {
		t.Fatalf("B = %d, want 2", UseB())
	}
}
