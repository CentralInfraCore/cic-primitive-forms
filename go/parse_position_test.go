package forms

import (
	"math/rand"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

// The positions of a text are found by decoding a line from its start. Doing that for every node is quadratic on a long line (issue #161: 80 000 items on one line took 12 s, 98% of it in
// table.pos). The table now remembers the offsets of a line as it finds them. These tests keep the answer the same as decoding from the line start, and keep the cost from growing with
// the square of the line again.

// positionFromTheLineStart is the old, plainly correct way: decode from the start of the line.
func positionFromTheLineStart(t *table, line, col int) *Pos {
	if line > len(t.lineStarts) {
		line = len(t.lineStarts)
	}
	off := t.lineStarts[line-1]
	for c := 1; c < col; c++ {
		_, size := utf8.DecodeRuneInString(t.body[off:])
		off += size
	}
	return &Pos{Line: line, Column: col, Offset: t.bomLen + off}
}

func TestTablePosIsTheSameAsDecodingFromTheLineStart(t *testing.T) {
	rng := rand.New(rand.NewSource(20261105))
	alphabet := []string{"a", "b", " ", ":", "[", "]", ",", "é", "ß", "世", "界", "\U0001F642", "\n", "\r\n", "\r"}
	for round := 0; round < 300; round++ {
		var b strings.Builder
		for i, n := 0, rng.Intn(120); i < n; i++ {
			b.WriteString(alphabet[rng.Intn(len(alphabet))])
		}
		body := b.String()
		bom := 0
		if round%7 == 0 {
			bom = 3
		}
		tab := newTable(body, bom)
		ref := newTable(body, bom)
		// columns asked for in any order, repeated, and past the end of the line (the old code goes on across the line break)
		for q := 0; q < 200; q++ {
			line := 1 + rng.Intn(len(tab.lineStarts)+1) // one past the last line is clamped
			col := rng.Intn(62) - 1                     // -1 and 0 too: below 1 is the start of the line
			got, want := tab.pos(line, col), positionFromTheLineStart(ref, line, col)
			if *got != *want {
				t.Fatalf("round %d body %q: pos(%d, %d) = %+v, decoding from the line start gives %+v", round, body, line, col, *got, *want)
			}
		}
	}
}

func TestParseOfALongLineIsNotQuadratic(t *testing.T) {
	// the limits of the old code were 12 s for 80 000 items on one line and 19 s for the refusal of 150 000; the bound is far above the linear cost (about 0.1 s) and far below the quadratic one
	const bound = 5 * time.Second
	shapes := map[string]string{
		"flow sequence on one line":         "w: [" + strings.TrimSuffix(strings.Repeat("0,", 90000), ",") + "]\n",
		"flow sequence over the node limit": "w: [" + strings.TrimSuffix(strings.Repeat("0,", 150000), ",") + "]\n",
		"nested flow sequences on one line": "w: [" + strings.TrimSuffix(strings.Repeat("[0],", 45000), ",") + "]\n",
		"flow mapping on one line":          "w: {" + strings.TrimSuffix(strings.Repeat("k: 1,", 1), ",") + manyKeys(40000) + "}\n",
	}
	for name, text := range shapes {
		start := time.Now()
		_, err := Parse(text)
		took := time.Since(start)
		if took > bound {
			t.Errorf("%s: %d bytes took %v (bound %v): the cost grows with the square of the line again", name, len(text), took, bound)
		}
		if name == "flow sequence over the node limit" {
			if err == nil || !strings.Contains(err.Error(), "LIMIT_EXCEEDED") {
				t.Errorf("%s: want the node limit, got %v", name, err)
			}
		} else if err != nil {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func manyKeys(n int) string {
	var b strings.Builder
	for i := 0; i < n; i++ {
		b.WriteString(", k")
		b.WriteString(strings.Repeat("x", 1+i%3))
		b.WriteString(itoa(i))
		b.WriteString(": 1")
	}
	return b.String()
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var d []byte
	for ; n > 0; n /= 10 {
		d = append([]byte{byte('0' + n%10)}, d...)
	}
	return string(d)
}
