// scipdeps prints the object level dependency graph of a Rust crate from a SCIP index made by rust-analyzer, in the schema of
// typeddeps (the Go side): a node per item, its deps the items its source refers to, resolved by rust-analyzer.
//
// Usage: rust-analyzer scip <crate dir> --output index.scip && scipdeps <index.scip> <crate dir>
//
// A node is an item with a body or a declaration: a function, a method (an impl item), a struct, an enum, a union, a type alias,
// a const or a static. Its deps are the items that every occurrence inside the item's source range resolves to:
//
//   - a field or an enum variant is a dependency on the type that declares it;
//   - a type depends on every method of every impl block for it (inherent or of a trait), because a value of the type can reach
//     them without naming them: Display::fmt through format!, PartialEq::eq through ==, Drop::drop at the end of a scope;
//   - an item of another crate appears under its own symbol and is listed in "external".
//
// Each integration test file (tests/<name>.rs) is a crate of its own; its items get the prefix "tests/<name>::". A test function
// that rust-analyzer does not define because a macro made it (proptest!) is found in the source text and gets a node over its
// text, so the occurrences in it still have an owner.
//
// What it does not model is reported: a trait of the crate itself (a call through it can dispatch to any implementer) marks
// the graph opaque, and the program fails.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/sourcegraph/scip/bindings/go/scip"
	"google.golang.org/protobuf/proto"
)

// Node is one item; the fields are those of typeddeps.
type Node struct {
	ID        string   `json:"id"`
	Kind      string   `json:"kind"` // func method type const
	File      string   `json:"file"`
	Line      int      `json:"line"` // 1-based
	EndLine   int      `json:"end_line"`
	Test      bool     `json:"test"`
	SrcSHA256 string   `json:"src_sha256"`
	Deps      []string `json:"deps"`
	Entry     bool     `json:"entry,omitempty"` // a test the runner starts: #[test], or a function of a proptest! block
	// Constructs are the language constructs this item itself contains (from `reportable`): what the extractor RECOGNISED, from the
	// SCIP symbols and the text of the item. Whether it models their dependencies is not said here: that is held outside it (the
	// Assurance of lab/measure/evidence.py, qualify_rust.py).
	Constructs []string `json:"constructs"`
	// Analysis: COMPLETE with nothing unresolved, or INCOMPLETE and why. An item found by its text (made by a macro) is INCOMPLETE.
	Analysis Analysis `json:"analysis"`
	nameLine int      // 0-based line of the defining name
	cons     map[string]bool
	unres    map[string]bool
}

// Analysis is the completeness of the extraction of one item.
type Analysis struct {
	Status     string   `json:"status"` // COMPLETE | INCOMPLETE
	Unresolved []string `json:"unresolved"`
}

// The constructs the extractor can report. rs.env.NAME and rs.macro.* are open-ended by name; the rest are listed.
var reportable = []string{"rs.fn", "rs.method", "rs.type", "rs.const", "rs.static", "rs.trait_impl", "rs.generic.lifetime",
	"rs.generic.type", "rs.macro.external", "rs.macro.local", "rs.macro.generated", "rs.macro.include", "rs.env.<NAME>", "rs.cfg.test",
	"rs.cfg.other"}

func (n *Node) add(c string) {
	if n.cons == nil {
		n.cons = map[string]bool{}
	}
	n.cons[c] = true
}

func (n *Node) unresolved(why string) {
	if n.unres == nil {
		n.unres = map[string]bool{}
	}
	n.unres[why] = true
}

// Graph is the output.
type Graph struct {
	Schema   string   `json:"schema"`
	Packages []string `json:"packages"`
	Scope    []string `json:"scope"` // the constructs the extractor can report: what "complete" is relative to
	Nodes    []Node   `json:"nodes"`
	External []string `json:"external"`
}

type span struct{ sl, sc, el, ec int } // 0-based, end exclusive

func spanOf(r []int32) span {
	if len(r) == 3 {
		return span{int(r[0]), int(r[1]), int(r[0]), int(r[2])}
	}
	return span{int(r[0]), int(r[1]), int(r[2]), int(r[3])}
}

func (s span) contains(line, col int) bool {
	if line < s.sl || line > s.el {
		return false
	}
	if line == s.sl && col < s.sc {
		return false
	}
	if line == s.el && col >= s.ec {
		return false
	}
	return true
}

func (s span) size() int { return (s.el-s.sl)*100000 + s.ec - s.sc }

type item struct {
	node *Node
	sp   span
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "scipdeps: "+format+"\n", args...)
	os.Exit(1)
}

// classify reads a descriptor of the crate: what kind of item it is, and the id of the item that owns it (a field or a variant
// belongs to its type). kind "" means not an item (a module, a macro, a parameter).
func classify(desc string) (kind, owner string) {
	switch {
	case strings.HasSuffix(desc, "/") || strings.HasSuffix(desc, ":") || strings.HasSuffix(desc, "]"):
		return "", ""
	case strings.HasSuffix(desc, ")."):
		if strings.Contains(desc, "impl#") || strings.Count(desc, "#") > 0 {
			return "method", desc
		}
		return "func", desc
	}
	// a type is "path/Name#"; a variant "path/Name#Variant#"; a field "path/Name#field."; a const or static "path/NAME."
	if i := strings.Index(desc, "#"); i >= 0 {
		if i == len(desc)-1 {
			return "type", desc
		}
		return "member", desc[:i+1]
	}
	if strings.HasSuffix(desc, ".") {
		return "const", desc
	}
	return "", ""
}

var (
	fnText   = regexp.MustCompile(`(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_]\w*)`)
	implSelf = regexp.MustCompile(`impl#\[([A-Za-z_]\w*)\]`)
)

var (
	genericHead  = regexp.MustCompile(`(?m)^\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?(?:(?:const|async|unsafe|default|extern(?:\s+"[^"]*")?)\s+)*(?:fn|struct|enum|union|type|trait)\s+[A-Za-z_]\w*\s*<`)
	envMacro     = regexp.MustCompile(`\b(?:env|option_env)!\s*\(\s*"([^"]*)"`)
	includeMacro = regexp.MustCompile(`\b(?:include_str|include_bytes|include)!`)
	cfgAttr      = regexp.MustCompile(`#!?\[\s*cfg(?:_attr)?\s*\(|\bcfg!\s*\(`)
	cfgTest      = regexp.MustCompile(`#!?\[\s*cfg\s*\(\s*test\s*\)\s*\]`)
)

// textConstructs adds what the text of an item shows: generic parameters, environment and include macros, cfg. The text is not parsed
// (strings and comments are not skipped), so a word in a comment can add a construct: the error is on the side of reporting too much.
func textConstructs(n *Node, text string) {
	for _, m := range genericHead.FindAllStringIndex(text, -1) {
		depth, start := 1, m[1]
		i := start
		for ; i < len(text) && depth > 0; i++ {
			switch text[i] {
			case '<':
				depth++
			case '>':
				depth--
			}
		}
		for _, param := range splitTop(text[start:max(start, i-1)]) {
			if strings.HasPrefix(strings.TrimSpace(param), "'") {
				n.add("rs.generic.lifetime")
			} else if strings.TrimSpace(param) != "" {
				n.add("rs.generic.type")
			}
		}
	}
	for _, m := range envMacro.FindAllStringSubmatch(text, -1) {
		n.add("rs.env." + m[1])
	}
	if includeMacro.MatchString(text) {
		n.add("rs.macro.include")
	}
	if cfgAttr.MatchString(text) {
		if cfgTest.MatchString(text) && len(cfgAttr.FindAllString(text, -1)) == len(cfgTest.FindAllString(text, -1)) {
			n.add("rs.cfg.test")
		} else {
			n.add("rs.cfg.other")
		}
	}
}

// splitTop splits a generic parameter list at the commas that are not inside <>, () or [].
func splitTop(s string) []string {
	var out []string
	depth, from := 0, 0
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '<', '(', '[':
			depth++
		case '>', ')', ']':
			depth--
		case ',':
			if depth == 0 {
				out = append(out, s[from:i])
				from = i + 1
			}
		}
	}
	return append(out, s[from:])
}

// implGroups counts the bracket groups after "impl#" in a descriptor (backticks quote a type that holds brackets): one is an inherent
// impl, two a trait impl; angle brackets inside show generic parameters.
func implGroups(desc string) (groups int, generic bool) {
	i := strings.Index(desc, "impl#")
	if i < 0 {
		return 0, false
	}
	rest := desc[i+len("impl#"):]
	for len(rest) > 0 && rest[0] == '[' {
		j, quoted := 1, false
		for ; j < len(rest); j++ {
			if rest[j] == '`' {
				quoted = !quoted
			}
			if rest[j] == ']' && !quoted {
				break
			}
		}
		if j >= len(rest) {
			break
		}
		if strings.Contains(rest[:j], "<") {
			generic = true
		}
		groups++
		rest = rest[j+1:]
	}
	return groups, generic
}

// bodyEnd finds the brace that closes the first block after offset, skipping comments, strings, raw strings and chars.
func bodyEnd(src []byte, from int) int {
	depth, i := 0, from
	for i < len(src) {
		c := src[i]
		switch {
		case c == '/' && i+1 < len(src) && src[i+1] == '/':
			for i < len(src) && src[i] != '\n' {
				i++
			}
			continue
		case c == '/' && i+1 < len(src) && src[i+1] == '*':
			i += 2
			for i+1 < len(src) && !(src[i] == '*' && src[i+1] == '/') {
				i++
			}
			i += 2
			continue
		case c == 'r' && i+1 < len(src) && (src[i+1] == '"' || src[i+1] == '#') && (i == 0 || !isIdent(src[i-1])):
			j, hashes := i+1, 0
			for j < len(src) && src[j] == '#' {
				hashes++
				j++
			}
			if j < len(src) && src[j] == '"' {
				end := "\"" + strings.Repeat("#", hashes)
				k := strings.Index(string(src[j+1:]), end)
				if k < 0 {
					return -1
				}
				i = j + 1 + k + len(end)
				continue
			}
		case c == '"':
			i++
			for i < len(src) && src[i] != '"' {
				if src[i] == '\\' {
					i++
				}
				i++
			}
			i++
			continue
		case c == '\'' && i+2 < len(src) && (src[i+2] == '\'' || src[i+1] == '\\'):
			i++
			for i < len(src) && src[i] != '\'' {
				if src[i] == '\\' {
					i++
				}
				i++
			}
			i++
			continue
		case c == '{':
			depth++
		case c == '}':
			depth--
			if depth == 0 {
				return i
			}
		}
		i++
	}
	return -1
}

// hasTestAttr reports whether the attribute and doc lines right above a function's name line include #[test].
func hasTestAttr(src []byte, nameLine int) bool {
	lines := strings.Split(string(src), "\n")
	for l := nameLine - 1; l >= 0 && l >= nameLine-30; l-- {
		t := strings.TrimSpace(lines[l])
		switch {
		case strings.HasPrefix(t, "#[test]"):
			return true
		case strings.HasPrefix(t, "#[") || strings.HasPrefix(t, "///") || strings.HasPrefix(t, "//"):
			continue
		}
		return false
	}
	return false
}

// macroTest reports whether a range rust-analyzer gave several items is a proptest! call: its functions are tests.
func macroTest(src []byte, sp span) bool {
	off := offset(src, sp.sl, sp.sc)
	return strings.HasPrefix(strings.TrimSpace(string(src[off:])), "proptest!")
}

func isIdent(c byte) bool {
	return c == '_' || c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z'
}

func lineCol(src []byte, off int) (int, int) {
	line := strings.Count(string(src[:off]), "\n")
	return line, off - (strings.LastIndex(string(src[:off]), "\n") + 1)
}

func offset(src []byte, line, col int) int {
	off := 0
	for l := 0; l < line; l++ {
		k := strings.IndexByte(string(src[off:]), '\n')
		if k < 0 {
			return len(src)
		}
		off += k + 1
	}
	return off + col
}

// plantedPlainSelfOnly brings back the rule before implSelfName: only an impl whose self type is a bare name (impl#[Name]) is reached by
// its type; one for a type with a lifetime or a type parameter (impl#[`Name<'_>`]) is not. A planted defect.
var plantedPlainSelfOnly = os.Getenv("SCIPDEPS_PLANTED_PLAIN_SELF_ONLY") == "1"

var selfName = regexp.MustCompile(`^&?(?:'\w+\s+)?(?:mut\s+)?([A-Za-z_]\w*)(?:<.*)?$`)

// implSelfName is the name of the type an impl block is for, read from the first bracket of its descriptor: `impl#[Name]...` or, when the
// type has parameters, a quoted type: impl#[`Name<'_>`]... (also `&Name`). "" when there is no named type (a slice, a tuple, a closure).
func implSelfName(desc string) string {
	if plantedPlainSelfOnly {
		if g := implSelf.FindStringSubmatch(desc); g != nil {
			return g[1]
		}
		return ""
	}
	i := strings.Index(desc, "impl#[")
	if i < 0 {
		return ""
	}
	rest := desc[i+len("impl#["):]
	quoted, j := false, 0
	for ; j < len(rest); j++ {
		if rest[j] == '`' {
			quoted = !quoted
		}
		if rest[j] == ']' && !quoted {
			break
		}
	}
	if j >= len(rest) {
		return ""
	}
	if g := selfName.FindStringSubmatch(strings.Trim(rest[:j], "`")); g != nil {
		return g[1]
	}
	return ""
}

// plantedNoImpl leaves out the type -> impl method edges: a planted defect, for showing that the measurement sees it.
var plantedNoImpl = os.Getenv("SCIPDEPS_PLANTED_NO_IMPL") == "1"

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: scipdeps <index.scip> <crate dir>")
		os.Exit(2)
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		fail("%v", err)
	}
	var idx scip.Index
	if err := proto.Unmarshal(raw, &idx); err != nil {
		fail("%v", err)
	}
	root := os.Args[2]

	// the crate's own package: the one of the documents' definitions
	pkgPrefix := ""
	for _, d := range idx.Documents {
		for _, s := range d.Symbols {
			if strings.HasPrefix(d.RelativePath, "src/") && !strings.HasPrefix(s.Symbol, "local ") {
				sym, err := scip.ParseSymbol(s.Symbol)
				if err == nil && sym.Package != nil {
					pkgPrefix = strings.Join([]string{sym.Scheme, sym.Package.Manager, sym.Package.Name, sym.Package.Version}, " ") + " "
				}
				break
			}
		}
		if pkgPrefix != "" {
			break
		}
	}
	if pkgPrefix == "" {
		fail("no definition in src/: is this a SCIP index of a crate?")
	}

	nodes := map[string]*Node{}
	deps := map[string]map[string]bool{}
	external := map[string]bool{}
	typesByName := map[string][]string{}
	var methods []string
	localTraits := []string{}

	type docItems struct {
		items   []item
		defined map[string]bool // descriptors defined in this document (a test crate's own items)
		prefix  string
		src     []byte
	}
	docs := map[string]*docItems{}

	for _, d := range idx.Documents {
		src, err := os.ReadFile(filepath.Join(root, d.RelativePath))
		if err != nil {
			fail("%v", err)
		}
		di := &docItems{defined: map[string]bool{}, src: src}
		if strings.HasPrefix(d.RelativePath, "tests/") {
			di.prefix = "tests/" + strings.TrimSuffix(filepath.Base(d.RelativePath), ".rs") + "::"
		}
		docs[d.RelativePath] = di
		kinds := map[string]scip.SymbolInformation_Kind{}
		for _, s := range d.Symbols {
			kinds[s.Symbol] = s.Kind
		}
		defLines := map[int]bool{}
		for _, o := range d.Occurrences {
			if o.SymbolRoles&int32(scip.SymbolRole_Definition) == 0 || !strings.HasPrefix(o.Symbol, pkgPrefix) {
				continue
			}
			desc := strings.TrimPrefix(o.Symbol, pkgPrefix)
			di.defined[desc] = true
			if kinds[o.Symbol] == scip.SymbolInformation_Trait {
				localTraits = append(localTraits, desc)
			}
			kind, _ := classify(desc)
			if kind == "" || kind == "member" || len(o.EnclosingRange) == 0 {
				continue
			}
			sp := spanOf(o.EnclosingRange)
			id := di.prefix + desc
			if _, dup := nodes[id]; dup {
				continue // a second definition site of the same symbol (rust-analyzer reports some twice)
			}
			n := &Node{ID: id, Kind: kind, File: d.RelativePath, Line: sp.sl + 1, EndLine: sp.el + 1,
				Test: di.prefix != "" || strings.Contains(desc, "tests/")}
			sum := sha256.Sum256(src[offset(src, sp.sl, sp.sc):offset(src, sp.el, sp.ec)])
			n.SrcSHA256 = hex.EncodeToString(sum[:])
			n.nameLine = spanOf(o.Range).sl
			switch {
			case kind == "func":
				n.add("rs.fn")
			case kind == "method":
				n.add("rs.method")
				groups, generic := implGroups(desc)
				if groups >= 2 {
					n.add("rs.trait_impl")
				}
				if generic {
					n.add("rs.generic.type")
				}
			case kind == "type":
				n.add("rs.type")
			case kinds[o.Symbol] == scip.SymbolInformation_StaticVariable:
				n.add("rs.static")
			default:
				n.add("rs.const")
			}
			nodes[id] = n
			deps[id] = map[string]bool{}
			di.items = append(di.items, item{n, sp})
			defLines[spanOf(o.Range).sl] = true
			if kind == "type" {
				name := strings.TrimSuffix(desc[strings.LastIndex(strings.TrimSuffix(desc, "#"), "/")+1:], "#")
				typesByName[name] = append(typesByName[name], id)
			}
			if kind == "method" {
				methods = append(methods, id)
			}
		}
		// items whose range rust-analyzer gives as the whole macro call that made them (several tests of one proptest! block share
		// it): each gets the range of its own text, from its name to the end of its body
		bySpan := map[span][]int{}
		for i, it := range di.items {
			bySpan[it.sp] = append(bySpan[it.sp], i)
		}
		for sp, list := range bySpan {
			if len(list) < 2 {
				continue
			}
			for _, i := range list {
				it := &di.items[i]
				nameLine := it.node.nameLine
				off := offset(src, nameLine, 0)
				end := bodyEnd(src, off)
				if end < 0 || it.node.Kind != "func" {
					fail("%s: items share the range %v and one is not a function with a body: %s", d.RelativePath, sp, it.node.ID)
				}
				el, ec := lineCol(src, end)
				it.sp = span{nameLine, 0, el, ec + 1}
				it.node.Line, it.node.EndLine = nameLine+1, el+1
				sum := sha256.Sum256(src[off : end+1])
				it.node.SrcSHA256 = hex.EncodeToString(sum[:])
				it.node.Entry = macroTest(src, sp)
				it.node.add("rs.macro.generated")
				it.node.unresolved("made by a macro call (" + filepath.Base(d.RelativePath) + "): the range is that of the call; the item is found by its text")
			}
		}
		for _, it := range di.items {
			textConstructs(it.node, string(src[offset(src, it.sp.sl, it.sp.sc):offset(src, it.sp.el, it.sp.ec)]))
		}
		for i := range di.items {
			if n := di.items[i].node; n.Kind == "func" && hasTestAttr(src, n.nameLine) {
				n.Entry = true
			}
		}
		// test functions a macro made (proptest!): no definition, so a node over their text
		for _, m := range fnText.FindAllSubmatchIndex(src, -1) {
			nameStart := m[2]
			line, _ := lineCol(src, nameStart)
			if defLines[line] {
				continue
			}
			end := bodyEnd(src, m[1])
			if end < 0 {
				fail("%s: no body found for fn %s", d.RelativePath, src[m[2]:m[3]])
			}
			el, ec := lineCol(src, end)
			sl, sc := lineCol(src, m[0])
			desc := string(src[m[2]:m[3]]) + "()."
			id := di.prefix + "<macro>/" + desc
			if _, dup := nodes[id]; dup {
				fail("%s: two macro-made functions named %s", d.RelativePath, desc)
			}
			sum := sha256.Sum256(src[m[0] : end+1])
			n := &Node{ID: id, Kind: "func", File: d.RelativePath, Line: sl + 1, EndLine: el + 1, Test: true,
				SrcSHA256: hex.EncodeToString(sum[:])}
			n.add("rs.fn")
			n.add("rs.macro.generated")
			n.unresolved("not defined by rust-analyzer: made by a macro, found by its text")
			textConstructs(n, string(src[m[0]:end+1]))
			nodes[id] = n
			deps[id] = map[string]bool{}
			di.items = append(di.items, item{n, span{sl, sc, el, ec + 1}})
		}
	}
	if len(localTraits) > 0 {
		sort.Strings(localTraits)
		fail("the crate defines traits (%s): a call through one can dispatch to any implementer, which this graph does not model",
			strings.Join(localTraits, ", "))
	}

	// edges: every occurrence of a crate symbol inside an item's range
	for _, d := range idx.Documents {
		di := docs[d.RelativePath]
		for _, o := range d.Occurrences {
			if strings.HasPrefix(o.Symbol, "local ") || o.Symbol == "" {
				continue
			}
			r := spanOf(o.Range)
			var owner *item
			for i := range di.items {
				it := &di.items[i]
				if it.sp.contains(r.sl, r.sc) && (owner == nil || it.sp.size() < owner.sp.size()) {
					owner = it
				}
			}
			if owner == nil {
				continue // a use declaration, a module or an attribute outside every item
			}
			if strings.HasSuffix(o.Symbol, "!") {
				// a macro call. The call site shows its arguments (they are indexed) but not what the macro expands to: for a macro of
				// the crate itself there is no edge to its body, which is a dependency the graph does not have.
				if strings.HasPrefix(o.Symbol, pkgPrefix) {
					owner.node.add("rs.macro.local")
				} else {
					owner.node.add("rs.macro.external")
				}
			}
			var target string
			if strings.HasPrefix(o.Symbol, pkgPrefix) {
				desc := strings.TrimPrefix(o.Symbol, pkgPrefix)
				kind, own := classify(desc)
				if kind == "" {
					continue
				}
				if kind == "member" {
					desc = own
				}
				if di.prefix != "" && di.defined[desc] {
					target = di.prefix + desc
				} else {
					target = desc
				}
			} else {
				f := strings.SplitN(o.Symbol, " ", 5) // scheme, manager, package, version, descriptors
				if len(f) < 5 {
					continue
				}
				target = f[2] + "::" + f[4]
				external[target] = true
			}
			if target != owner.node.ID {
				deps[owner.node.ID][target] = true
			}
		}
	}
	// a type reaches every method of its impl blocks
	for _, m := range methods {
		if plantedNoImpl {
			break
		}
		name := implSelfName(m)
		if name == "" {
			continue
		}
		for _, t := range typesByName[name] {
			deps[t][m] = true
		}
	}

	scope := append([]string{}, reportable...)
	sort.Strings(scope)
	out := Graph{Schema: "cic-lab/typeddeps/v2", Scope: scope, Packages: []string{strings.TrimSpace(pkgPrefix)}, Nodes: []Node{}, External: []string{}}
	for id, n := range nodes {
		n.Constructs = []string{}
		for c := range n.cons {
			n.Constructs = append(n.Constructs, c)
		}
		sort.Strings(n.Constructs)
		n.Analysis = Analysis{Status: "COMPLETE", Unresolved: []string{}}
		for u := range n.unres {
			n.Analysis.Unresolved = append(n.Analysis.Unresolved, u)
		}
		sort.Strings(n.Analysis.Unresolved)
		if len(n.Analysis.Unresolved) > 0 {
			n.Analysis.Status = "INCOMPLETE"
		}
		n.Deps = []string{}
		for d := range deps[id] {
			n.Deps = append(n.Deps, d)
		}
		sort.Strings(n.Deps)
		out.Nodes = append(out.Nodes, *n)
	}
	sort.Slice(out.Nodes, func(i, j int) bool { return out.Nodes[i].ID < out.Nodes[j].ID })
	for e := range external {
		out.External = append(out.External, e)
	}
	sort.Strings(out.External)
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(out); err != nil {
		fail("%v", err)
	}
}
