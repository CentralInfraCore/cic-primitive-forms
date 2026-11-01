package genericrepro

import "testing"

func TestId(t *testing.T) {
	if Id(1) != 1 {
		t.Fatal("Id")
	}
}
