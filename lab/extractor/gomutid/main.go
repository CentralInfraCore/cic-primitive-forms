// The lab's copy of tools/c0/mutid (main, unchanged otherwise). Two changes: a mutant whose token stream equals the original
// (go-mutesting changed only layout or comments: the same program) is reported on stderr and left out instead of failing the run,
// and every row ends with the line of the first difference in the original (1-based), so the mutant can be placed on a graph.
//
// Prototype of the C0 study (docs/C0-GO-MUTANT-ID-STUDY.md): a candidate stable id for a go-mutesting mutant, derived from the original
// file and the mutated file alone, without line numbers and without running a test.
//
//	mutid ids     -orig DIR -mut DIR -index index.tsv     one line per mutant: stable id, function, change, ordinal, body digest
//	mutid closure -src DIR                                per function: the functions whose change can reach it (name based call graph)
//
// The id is sha256(file | enclosing function | changed tokens before | changed tokens after | ordinal)[:16]. The body digest is the
// digest of the token stream of the enclosing function: it moves when the function changes and not when anything else does.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/scanner"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type tok struct {
	t   token.Token
	lit string
	off int
}

func (t tok) text() string {
	if t.lit != "" {
		return t.lit
	}
	return t.t.String()
}

func tokens(src []byte) []tok {
	fset := token.NewFileSet()
	f := fset.AddFile("", fset.Base(), len(src))
	var s scanner.Scanner
	s.Init(f, src, nil, 0) // comments are not tokens: a comment moves nothing
	var out []tok
	for {
		_, t, lit := s.Scan()
		if t == token.EOF {
			return out
		}
		out = append(out, tok{t: t, lit: lit})
	}
}

// offsets fills in the byte offset of every token, for locating the enclosing function.
func offsets(src []byte) []int {
	fset := token.NewFileSet()
	f := fset.AddFile("", fset.Base(), len(src))
	var s scanner.Scanner
	s.Init(f, src, nil, 0)
	var out []int
	for {
		pos, t, _ := s.Scan()
		if t == token.EOF {
			return out
		}
		out = append(out, f.Offset(pos))
	}
}

func join(ts []tok) string {
	parts := make([]string, len(ts))
	for i, t := range ts {
		parts[i] = t.text()
	}
	return strings.Join(parts, " ")
}

func sum(parts ...string) string {
	h := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return hex.EncodeToString(h[:])
}

// fn is a function or method of the original file with the token range of its text.
type fn struct {
	name       string
	start, end int // byte offsets
	digest     string
}

func qualified(d *ast.FuncDecl) string {
	if d.Recv == nil || len(d.Recv.List) == 0 {
		return d.Name.Name
	}
	t := d.Recv.List[0].Type
	for {
		switch x := t.(type) {
		case *ast.StarExpr:
			t = x.X
			continue
		case *ast.IndexExpr:
			t = x.X
			continue
		case *ast.IndexListExpr:
			t = x.X
			continue
		case *ast.Ident:
			return x.Name + "." + d.Name.Name
		}
		return "?." + d.Name.Name
	}
}

func functions(src []byte) ([]fn, error) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "", src, 0) // no comments kept
	if err != nil {
		return nil, err
	}
	var out []fn
	for _, d := range file.Decls {
		switch x := d.(type) {
		case *ast.FuncDecl:
			s, e := fset.Position(x.Pos()).Offset, fset.Position(x.End()).Offset
			out = append(out, fn{name: qualified(x), start: s, end: e, digest: sum(join(tokens(src[s:e])))})
		case *ast.GenDecl: // a package level var, const or type: one unit per declaration
			s, e := fset.Position(x.Pos()).Offset, fset.Position(x.End()).Offset
			label := x.Tok.String()
			for _, sp := range x.Specs {
				switch v := sp.(type) {
				case *ast.ValueSpec:
					label += " " + v.Names[0].Name
				case *ast.TypeSpec:
					label += " " + v.Name.Name
				}
				break
			}
			out = append(out, fn{name: "<" + label + ">", start: s, end: e, digest: sum(join(tokens(src[s:e])))})
		}
	}
	return out, nil
}

type mutant struct {
	oldID, file     string
	fn, from, to    string
	at              int // token index of the first difference in the original
	ordinal         int
	id, body, whole string
	line            int // of the first difference in the original, 1-based
}

// offsetOfToken is the byte offset of the token with index i in the original's token stream.
func offsetOfToken(src []byte, i int) int {
	offs := offsets(src)
	if i < len(offs) {
		return offs[i]
	}
	return len(src)
}

func derive(file string, orig, mut []byte, funcs []fn) (mutant, error) {
	a, b := tokens(orig), tokens(mut)
	p := 0
	for p < len(a) && p < len(b) && a[p].t == b[p].t && a[p].lit == b[p].lit {
		p++
	}
	sa, sb := len(a), len(b)
	for sa > p && sb > p && a[sa-1].t == b[sb-1].t && a[sa-1].lit == b[sb-1].lit {
		sa--
		sb--
	}
	if p == sa && p == sb {
		return mutant{}, fmt.Errorf("%s: the token streams are equal, there is no change to name", file)
	}
	offs := offsets(orig)
	at := len(offs) - 1
	if p < len(offs) {
		at = p
	}
	off := offs[at]
	name := "<none>"
	body := ""
	for _, f := range funcs {
		if off >= f.start && off < f.end {
			name, body = f.name, f.digest
			break
		}
	}
	return mutant{file: file, fn: name, from: join(a[p:sa]), to: join(b[p:sb]), at: p, body: body,
		whole: sum(string(orig))}, nil
}

func cmdIDs(args []string) error {
	fs := flag.NewFlagSet("ids", flag.ExitOnError)
	origDir := fs.String("orig", "", "directory of the original files")
	mutDir := fs.String("mut", "", "directory of the mutated files, <old id>.go")
	index := fs.String("index", "", "index.tsv: <old id> <file>")
	_ = fs.Parse(args)
	raw, err := os.ReadFile(*index)
	if err != nil {
		return err
	}
	funcsOf := map[string][]fn{}
	origOf := map[string][]byte{}
	var ms []mutant
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		f := strings.Split(line, "\t")
		if len(f) != 2 {
			return fmt.Errorf("bad index line %q", line)
		}
		old, file := f[0], f[1]
		if _, ok := origOf[file]; !ok {
			o, err := os.ReadFile(filepath.Join(*origDir, file))
			if err != nil {
				return err
			}
			origOf[file] = o
			if funcsOf[file], err = functions(o); err != nil {
				return err
			}
		}
		mut, err := os.ReadFile(filepath.Join(*mutDir, old+".go"))
		if err != nil {
			return err
		}
		m, err := derive(file, origOf[file], mut, funcsOf[file])
		if err != nil && strings.Contains(err.Error(), "the token streams are equal") {
			fmt.Fprintf(os.Stderr, "same program\t%s\t%s\n", old, file)
			continue
		}
		if err != nil {
			return err
		}
		m.line = 1 + strings.Count(string(origOf[file][:offsetOfToken(origOf[file], m.at)]), "\n")
		m.oldID = old
		ms = append(ms, m)
	}
	// the ordinal of a (file, function, before, after) among its equals, in the order of the position in the original
	sort.SliceStable(ms, func(i, j int) bool {
		if ms[i].file != ms[j].file {
			return ms[i].file < ms[j].file
		}
		return ms[i].at < ms[j].at
	})
	count := map[string]int{}
	for i := range ms {
		k := strings.Join([]string{ms[i].file, ms[i].fn, ms[i].from, ms[i].to}, "\x00")
		count[k]++
		ms[i].ordinal = count[k]
		ms[i].id = sum(ms[i].file, ms[i].fn, ms[i].from, ms[i].to, fmt.Sprint(ms[i].ordinal))[:16]
	}
	for _, m := range ms {
		fmt.Printf("%s\t%s\t%s\t%s\t%s\t%d\t%s\t%s\t%d\n", m.id, m.file, m.fn, oneLine(m.from), oneLine(m.to), m.ordinal, m.body[:16], m.oldID, m.line)
	}
	return nil
}

func oneLine(s string) string {
	s = strings.ReplaceAll(s, "\t", " ")
	s = strings.ReplaceAll(s, "\n", " ")
	if len(s) > 60 {
		s = s[:60] + "..."
	}
	return s
}

// closure: for every function of the package, the set of functions it can reach by calls (a name based over approximation: a call
// of a selector `x.m()` may reach every method called m), so the functions whose change can change its behaviour.
func cmdClosure(args []string) error {
	fs := flag.NewFlagSet("closure", flag.ExitOnError)
	src := fs.String("src", "", "package directory")
	_ = fs.Parse(args)
	files, _ := filepath.Glob(filepath.Join(*src, "*.go"))
	fset := token.NewFileSet()
	byName := map[string][]string{} // simple name -> qualified names
	calls := map[string]map[string]bool{}
	var all []string
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		file, err := parser.ParseFile(fset, f, nil, 0)
		if err != nil {
			return err
		}
		for _, d := range file.Decls {
			x, ok := d.(*ast.FuncDecl)
			if !ok {
				continue
			}
			q := qualified(x)
			all = append(all, q)
			byName[x.Name.Name] = append(byName[x.Name.Name], q)
			calls[q] = map[string]bool{}
			if x.Body == nil {
				continue
			}
			ast.Inspect(x.Body, func(n ast.Node) bool {
				switch c := n.(type) {
				case *ast.CallExpr:
					switch e := c.Fun.(type) {
					case *ast.Ident:
						calls[q][e.Name] = true
					case *ast.SelectorExpr:
						calls[q][e.Sel.Name] = true
					}
				case *ast.Ident: // a function used as a value
					calls[q][c.Name] = true
				}
				return true
			})
		}
	}
	sort.Strings(all)
	for _, q := range all {
		seen := map[string]bool{q: true}
		stack := []string{q}
		for len(stack) > 0 {
			cur := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			for name := range calls[cur] {
				for _, t := range byName[name] {
					if !seen[t] {
						seen[t] = true
						stack = append(stack, t)
					}
				}
			}
		}
		var list []string
		for t := range seen {
			list = append(list, t)
		}
		sort.Strings(list)
		fmt.Printf("%s\t%s\n", q, strings.Join(list, ","))
	}
	return nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: mutid ids|closure ...")
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "ids":
		err = cmdIDs(os.Args[2:])
	case "closure":
		err = cmdClosure(os.Args[2:])
	default:
		err = fmt.Errorf("unknown command %q", os.Args[1])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "mutid:", err)
		os.Exit(1)
	}
}
