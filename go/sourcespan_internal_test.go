package forms

// The scanner and the read-back of SourceSpan, directly (the Rust peer is the unit tests of rust/src/sourcespan.rs).

import "testing"

func TestTheScannerStopsWhereTheRulesSay(t *testing.T) {
	type want struct {
		end   int
		style SpanStyle
		code  string
	}
	for _, c := range []struct {
		text  string
		start int
		want  want
	}{
		{"name: alpha\n", 6, want{11, SpanPlain, ""}},
		{"name: alpha   # c\n", 6, want{11, SpanPlain, ""}},
		{"name: a#b\n", 6, want{9, SpanPlain, ""}},  // a # not after a space is part of the scalar
		{"name: alpha", 6, want{11, SpanPlain, ""}}, // no final newline
		{"name: 'it''s'\n", 6, want{13, SpanSingleQuoted, ""}},
		{"name: \"a\\\"b\"\n", 6, want{12, SpanDoubleQuoted, ""}},
		{"name: |\n", 6, want{0, "", "EDIT_BLOCK_SCALAR"}},
		{"name: >\n", 6, want{0, "", "EDIT_BLOCK_SCALAR"}},
		{"name: 'a\nb'\n", 6, want{0, "", "EDIT_MULTILINE_SCALAR"}},
		{"name: 'a", 6, want{0, "", "EDIT_MULTILINE_SCALAR"}},
		{"name: \"a\nb\"\n", 6, want{0, "", "EDIT_MULTILINE_SCALAR"}},
		{"name: \"a\\\nb\"\n", 6, want{0, "", "EDIT_MULTILINE_SCALAR"}}, // an escaped line break
		{"name: \"a\\", 6, want{0, "", "EDIT_MULTILINE_SCALAR"}},
		{"name: \"a", 6, want{0, "", "EDIT_MULTILINE_SCALAR"}},
		{"a: b\r\nc: d\r\n", 3, want{4, SpanPlain, ""}}, // the span stops before the CR
		{"a: b\rc: d\r", 3, want{4, SpanPlain, ""}},     // and before a lone CR
	} {
		end, style, code := scanSpan(c.text, c.start)
		if (want{end, style, code}) != c.want {
			t.Errorf("%q from %d: got %d %q %q, want %v", c.text, c.start, end, style, code, c.want)
		}
	}
}

func TestASpanThatDoesNotReadBackToTheValueIsADefectNotAGuess(t *testing.T) {
	node := func(text string) *Node {
		n, err := Parse(text)
		if err != nil {
			t.Fatal(err)
		}
		return n
	}
	for _, c := range []struct {
		token string
		value string
		style SpanStyle
		want  string
	}{
		{"'alpha'", "alpha!", SpanSingleQuoted, "EDIT_SPAN_MISMATCH"},
		{"\"alpha\"", "x", SpanDoubleQuoted, "EDIT_SPAN_MISMATCH"},
		{"gamma", "alpha", SpanPlain, "EDIT_SPAN_MISMATCH"},
		{"'alpha'", "alpha beta", SpanSingleQuoted, "EDIT_SPAN_MISMATCH"}, // only a plain scalar can be multi-line
		{"alpha", "alpha beta", SpanPlain, "EDIT_MULTILINE_SCALAR"},
		{"alpha", "beta alpha", SpanPlain, "EDIT_SPAN_MISMATCH"},
		{"alpha", "alph", SpanPlain, "EDIT_SPAN_MISMATCH"},
		{"alpha", "alpha", SpanPlain, ""},
		{"'a'", "a", SpanSingleQuoted, ""},
		{"3", "3", SpanPlain, ""},
		{"True", "true", SpanPlain, ""},
	} {
		if got := readBack(c.token, node(c.value), c.style); got != c.want {
			t.Errorf("%q against %q (%s): got %q, want %q", c.token, c.value, c.style, got, c.want)
		}
	}
}
