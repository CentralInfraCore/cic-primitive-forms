package inhrepro

import "testing"

func TestB(t *testing.T) {
	if UseB() != 11 {
		t.Fatalf("B = %d, want 11", UseB())
	}
}
