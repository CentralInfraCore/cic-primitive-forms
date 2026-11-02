// typeddeps prints the object level dependency graph of Go packages, read with go/packages and go/types.
//
// Usage: typeddeps [-tests] <dir> [<pattern> ...]   (patterns default to ./...)
//
// A node is a top-level declaration: a function, a method, a type, a package-level variable or constant (one node per
// name of a value spec). Its id is the import path, then the receiver type for a method, then the name:
// "example.com/m.T.M". Its deps are the ids of every top-level object that an identifier in the declaration resolves to,
// by the type checker (Info.Uses), whatever the object's name or package:
//
//   - a use of a struct field is a dependency on the named type that declares the field;
//   - a call of an interface method is a dependency on the interface method and on that method of every named type of the
//     loaded packages that implements the interface (the call can dispatch to any of them);
//   - an object of a package that was not loaded (the standard library, a module dependency) appears under its own id and
//     is listed in "external" as well.
//
// What it cannot see is reported, not guessed: a declaration that uses reflect, unsafe or a go:linkname directive, and a
// file with a go:embed directive, is marked "opaque" (the edges it has are real, but there can be others).
//
// The graph is meant for deciding what a change can reach. It fails closed: a package that does not type check is an
// error, never a smaller graph.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"golang.org/x/tools/go/packages"
)

// Node is one top-level declaration.
type Node struct {
	ID        string   `json:"id"`
	Kind      string   `json:"kind"` // func method type var const
	File      string   `json:"file"` // relative to the directory given
	Line      int      `json:"line"`
	EndLine   int      `json:"end_line"`
	Test      bool     `json:"test"`       // declared in a _test.go file
	SrcSHA256 string   `json:"src_sha256"` // of the declaration's source text (the spec for a type, var or const)
	Deps      []string `json:"deps"`
	Opaque    []string `json:"opaque,omitempty"` // why the deps may be incomplete
	Entry     bool     `json:"entry,omitempty"`  // a test the runner starts: TestX / FuzzX of a _test.go file
	// Constructs are the language constructs this declaration itself contains (not its dependencies'), from the list in
	// `constructs` below. It is what the extractor RECOGNISED. Whether it models their dependencies correctly is not said here and
	// cannot be: that is a claim of whoever reviewed this extractor, held outside it (lab/measure/evidence.py, Assurance).
	Constructs []string `json:"constructs"`
	// Analysis says whether the walk over this declaration was complete in the scope the extractor declares: COMPLETE with no
	// unresolved item, or INCOMPLETE and what could not be resolved. Complete is not correct (see implicit iota).
	Analysis Analysis `json:"analysis"`
	// Group is the digest of the whole const/var declaration (`const ( ... )`) a spec belongs to. It is reported for measurement only
	// (lab/measure/iota_exposure.py): the key of a constant does not depend on it, which is the known iota defect.
	Group string `json:"group_sha256,omitempty"`
}

// Analysis is the completeness of the extraction of one declaration.
type Analysis struct {
	Status     string   `json:"status"` // COMPLETE | INCOMPLETE
	Unresolved []string `json:"unresolved"`
}

// The constructs the extractor can report; anything else it meets is unresolved, never silently absent.
var reportable = []string{"go.const.explicit", "go.const.group", "go.const.implicit_repeat", "go.const.iota", "go.func", "go.func.generic", "go.method",
	"go.type", "go.type.alias", "go.type.generic", "go.var"}

// Graph is the output.
type Graph struct {
	Schema   string   `json:"schema"`
	Packages []string `json:"packages"`
	Scope    []string `json:"scope"` // the constructs the extractor can report: what "complete" is relative to
	Nodes    []Node   `json:"nodes"`
	External []string `json:"external"`
}

func idOf(obj types.Object) string {
	if obj.Pkg() == nil {
		return ""
	}
	if fn, ok := obj.(*types.Func); ok {
		if recv := fn.Type().(*types.Signature).Recv(); recv != nil {
			if name := recvName(recv.Type()); name != "" {
				return obj.Pkg().Path() + "." + name + "." + obj.Name()
			}
		}
	}
	return obj.Pkg().Path() + "." + obj.Name()
}

func recvName(t types.Type) string {
	if p, ok := t.(*types.Pointer); ok {
		t = p.Elem()
	}
	switch x := t.(type) {
	case *types.Named:
		return x.Origin().Obj().Name()
	case *types.Alias:
		return recvName(types.Unalias(x))
	case *types.Interface:
		return "" // a method of an unnamed interface: no top-level owner
	}
	return ""
}

func origin(obj types.Object) types.Object {
	switch x := obj.(type) {
	case *types.Func:
		return x.Origin()
	case *types.Var:
		return x.Origin()
	}
	return obj
}

type builder struct {
	root     string
	fset     *token.FileSet
	loaded   map[string]bool            // import paths of the loaded packages
	fieldOf  map[*types.Var]string      // struct field -> id of the named type declaring it
	named    []*types.Named             // every package-level named type of the loaded packages
	nodes    map[string]*Node           // by id
	deps     map[string]map[string]bool // by id
	external map[string]bool
}

func (b *builder) indexTypes(pkg *packages.Package) {
	scope := pkg.Types.Scope()
	for _, name := range scope.Names() {
		tn, ok := scope.Lookup(name).(*types.TypeName)
		if !ok || tn.IsAlias() {
			continue
		}
		named, ok := tn.Type().(*types.Named)
		if !ok {
			continue
		}
		b.named = append(b.named, named)
		b.indexFields(named.Underlying(), idOf(tn), map[types.Type]bool{})
	}
}

// indexFields maps every field reachable inside the type literal (nested anonymous structs too) to the named type.
func (b *builder) indexFields(t types.Type, owner string, seen map[types.Type]bool) {
	if seen[t] {
		return
	}
	seen[t] = true
	switch x := t.(type) {
	case *types.Struct:
		for i := 0; i < x.NumFields(); i++ {
			f := x.Field(i)
			if _, done := b.fieldOf[f]; !done {
				b.fieldOf[f] = owner
			}
			if _, isNamed := f.Type().(*types.Named); !isNamed {
				b.indexFields(f.Type(), owner, seen)
			}
		}
	case *types.Pointer:
		b.indexFields(x.Elem(), owner, seen)
	case *types.Slice:
		b.indexFields(x.Elem(), owner, seen)
	case *types.Array:
		b.indexFields(x.Elem(), owner, seen)
	case *types.Map:
		b.indexFields(x.Key(), owner, seen)
		b.indexFields(x.Elem(), owner, seen)
	}
}

// targets returns the ids a use of obj makes the declaration depend on.
func (b *builder) targets(obj types.Object) []string {
	obj = origin(obj)
	switch x := obj.(type) {
	case *types.PkgName, *types.Label, *types.Builtin, *types.Nil:
		return nil
	case *types.Var:
		if x.IsField() {
			if owner, ok := b.fieldOf[x]; ok {
				return []string{owner}
			}
			if x.Pkg() != nil && !b.loaded[x.Pkg().Path()] {
				return []string{x.Pkg().Path() + ".<field>." + x.Name()}
			}
			return nil // a field of an unnamed local struct type: inside the declaration
		}
		if x.Parent() != x.Pkg().Scope() {
			return nil // a local variable or a parameter
		}
	case *types.Const, *types.TypeName:
		if x.Pkg() == nil || x.Parent() != x.Pkg().Scope() {
			return nil // universe, or declared inside a function
		}
	case *types.Func:
		sig := x.Type().(*types.Signature)
		if sig.Recv() != nil {
			if iface, ok := sig.Recv().Type().Underlying().(*types.Interface); ok {
				out := []string{}
				if id := idOf(x); id != "" && recvName(sig.Recv().Type()) != "" {
					out = append(out, id) // the universe's error.Error has no package, and no id
				}
				for _, n := range b.named {
					if plantedNoImplementers {
						break
					}
					for _, t := range []types.Type{n, types.NewPointer(n)} {
						if !types.Implements(t, iface) {
							continue
						}
						if m, _, _ := types.LookupFieldOrMethod(t, true, x.Pkg(), x.Name()); m != nil {
							out = append(out, idOf(origin(m)))
						}
						break
					}
				}
				return out
			}
		}
	}
	if obj.Pkg() == nil {
		return nil
	}
	return []string{idOf(obj)}
}

func (b *builder) add(pkg *packages.Package, file *ast.File, id, kind string, at, end token.Pos, src []byte, walk []ast.Node, opaque []string, declSig *types.Signature, constructs, unresolved []string) {
	pos, endPos := b.fset.Position(at), b.fset.Position(end)
	rel, err := filepath.Rel(b.root, pos.Filename)
	if err != nil {
		rel = pos.Filename
	}
	if old, ok := b.nodes[id]; ok {
		if old.File != rel || old.Line != pos.Line {
			fail("two declarations with the id %s (%s:%d, %s:%d)", id, old.File, old.Line, rel, pos.Line)
		}
		return // the same declaration seen in a second test variant of the package
	}
	sum := sha256.Sum256(src[pos.Offset:endPos.Offset])
	n := &Node{ID: id, Kind: kind, File: rel, Line: pos.Line, EndLine: endPos.Line, Test: strings.HasSuffix(rel, "_test.go"),
		SrcSHA256: hex.EncodeToString(sum[:]), Opaque: opaque}
	n.Constructs = append([]string{}, constructs...)
	sort.Strings(n.Constructs)
	n.Analysis = Analysis{Status: "COMPLETE", Unresolved: []string{}}
	if n.Test && kind == "func" {
		name := id[strings.LastIndex(id, ".")+1:]
		n.Entry = strings.HasPrefix(name, "Test") || strings.HasPrefix(name, "Fuzz")
	}
	b.nodes[id] = n
	deps := map[string]bool{}
	for _, w := range walk {
		guards := map[*ast.Ident]bool{} // `x` of `switch x := v.(type)`: an object per clause (Implicits), none for the name itself
		ast.Inspect(w, func(nd ast.Node) bool {
			if ts, ok := nd.(*ast.TypeSwitchStmt); ok {
				if as, ok := ts.Assign.(*ast.AssignStmt); ok && len(as.Lhs) == 1 {
					if id, ok := as.Lhs[0].(*ast.Ident); ok {
						guards[id] = true
					}
				}
			}
			return true
		})
		ast.Inspect(w, func(nd ast.Node) bool {
			ident, ok := nd.(*ast.Ident)
			if !ok {
				return true
			}
			// An instantiation (explicit or inferred) hands the type arguments to a body that may give them to fmt or another
			// interface: the declaration that instantiates depends on what the type arguments can dispatch to.
			if inst, isInst := pkg.TypesInfo.Instances[ident]; isInst && !plantedNoTypeArgs {
				m := map[string]bool{}
				for i := 0; i < inst.TypeArgs.Len(); i++ {
					ta := inst.TypeArgs.At(i)
					if ta == nil || ta == types.Typ[types.Invalid] {
						n.Opaque = appendOnce(n.Opaque, "a type argument that could not be resolved")
						continue
					}
					b.dispatch(ta, m, map[types.Type]bool{})
				}
				for t := range m {
					if t != id {
						deps[t] = true
					}
				}
			}
			obj := pkg.TypesInfo.Uses[ident]
			if obj == nil {
				if pkg.TypesInfo.Defs[ident] == nil && ident.Name != "_" && !guards[ident] {
					n.Analysis.Unresolved = appendOnce(n.Analysis.Unresolved, "identifier "+ident.Name+" resolves to no object")
				}
				return true
			}
			if _, isPkg := obj.(*types.PkgName); isPkg {
				return true
			}
			if p := obj.Pkg(); p != nil && (p.Path() == "unsafe" || p.Path() == "reflect" && !benignReflect[obj.Name()]) {
				n.Opaque = appendOnce(n.Opaque, "uses "+p.Path()+"."+obj.Name())
			}
			for _, t := range b.targets(obj) {
				if t != id {
					deps[t] = true
				}
			}
			return true
		})
	}
	finish(n, unresolved)
	if noConversions {
		b.deps[id] = deps
		return
	}
	for t := range b.conversions(pkg, walk, declSig) {
		if t != id {
			deps[t] = true
		}
	}
	b.deps[id] = deps
}

// finish is called once all of a declaration's walks are done.
func finish(n *Node, unresolved []string) {
	for _, u := range unresolved {
		n.Analysis.Unresolved = appendOnce(n.Analysis.Unresolved, u)
	}
	sort.Strings(n.Analysis.Unresolved)
	if len(n.Analysis.Unresolved) > 0 {
		n.Analysis.Status = "INCOMPLETE"
	}
}

// conversions finds where a value of a named type of the loaded packages becomes an interface value (an argument of an
// interface parameter, fmt's ...any among them; a return, an assignment, a composite literal element, a send, an explicit
// conversion). From there any method of the type can be called by dynamic dispatch (fmt calls String, errors.Is calls Unwrap),
// so the declaration depends on every method of the type and of its pointer.
func (b *builder) conversions(pkg *packages.Package, walk []ast.Node, declSig *types.Signature) map[string]bool {
	out := map[string]bool{}
	info := pkg.TypesInfo
	check := func(e ast.Expr, dst types.Type) {
		if e == nil || dst == nil || !types.IsInterface(dst) {
			return
		}
		if _, isParam := dst.(*types.TypeParam); isParam {
			return
		}
		src := info.TypeOf(e)
		if src == nil || types.IsInterface(src) {
			return
		}
		b.methodsOf(src, out)
	}
	sigs := []*types.Signature{declSig} // enclosing function signatures, for return statements (nil outside a function)
	for _, w := range walk {
		var stack []ast.Node
		ast.Inspect(w, func(nd ast.Node) bool {
			if nd == nil {
				top := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				if _, isLit := top.(*ast.FuncLit); isLit {
					sigs = sigs[:len(sigs)-1]
				}
				return true
			}
			stack = append(stack, nd)
			switch x := nd.(type) {
			case *ast.FuncLit:
				sig, _ := info.TypeOf(x).(*types.Signature)
				sigs = append(sigs, sig)
			case *ast.CallExpr:
				if tv, ok := info.Types[x.Fun]; ok && tv.IsType() {
					if len(x.Args) == 1 {
						check(x.Args[0], tv.Type)
					}
					return true
				}
				sig, ok := info.TypeOf(x.Fun).Underlying().(*types.Signature)
				if !ok {
					return true
				}
				params := sig.Params()
				for i, arg := range x.Args {
					var pt types.Type
					switch {
					case sig.Variadic() && i >= params.Len()-1 && !x.Ellipsis.IsValid():
						pt = params.At(params.Len() - 1).Type().(*types.Slice).Elem()
					case i < params.Len():
						pt = params.At(i).Type()
					}
					check(arg, pt)
				}
			case *ast.ReturnStmt:
				if len(sigs) > 0 && sigs[len(sigs)-1] != nil {
					res := sigs[len(sigs)-1].Results()
					if res.Len() == len(x.Results) {
						for i, r := range x.Results {
							check(r, res.At(i).Type())
						}
					}
				}
			case *ast.AssignStmt:
				if x.Tok != token.DEFINE && len(x.Lhs) == len(x.Rhs) {
					for i := range x.Lhs {
						check(x.Rhs[i], info.TypeOf(x.Lhs[i]))
					}
				}
			case *ast.ValueSpec:
				if x.Type != nil {
					for _, v := range x.Values {
						check(v, info.TypeOf(x.Type))
					}
				}
			case *ast.SendStmt:
				if ch, ok := info.TypeOf(x.Chan).Underlying().(*types.Chan); ok {
					check(x.Value, ch.Elem())
				}
			case *ast.CompositeLit:
				t := info.TypeOf(x)
				if t == nil {
					return true
				}
				switch u := t.Underlying().(type) {
				case *types.Struct:
					for i, el := range x.Elts {
						if kv, ok := el.(*ast.KeyValueExpr); ok {
							if f, ok := info.Uses[kv.Key.(*ast.Ident)].(*types.Var); ok {
								check(kv.Value, f.Type())
							}
						} else if i < u.NumFields() {
							check(el, u.Field(i).Type())
						}
					}
				case *types.Slice, *types.Array, *types.Map:
					var kt, et types.Type
					switch c := u.(type) {
					case *types.Slice:
						et = c.Elem()
					case *types.Array:
						et = c.Elem()
					case *types.Map:
						kt, et = c.Key(), c.Elem()
					}
					for _, el := range x.Elts {
						if kv, ok := el.(*ast.KeyValueExpr); ok {
							check(kv.Key, kt)
							check(kv.Value, et)
						} else {
							check(el, et)
						}
					}
				}
			}
			return true
		})
	}
	return out
}

// methodsOf adds the ids of the methods a value of type t can reach when it becomes an interface value (or is a type argument that a
// generic body hands to one): see dispatch. -planted-old-methodsof brings back the rule before that, which looked only at the methods
// declared on a named type.
func (b *builder) methodsOf(t types.Type, out map[string]bool) {
	if plantedOldMethodsOf {
		if p, ok := t.(*types.Pointer); ok {
			t = p.Elem()
		}
		named, ok := types.Unalias(t).(*types.Named)
		if !ok || named.Obj().Pkg() == nil || !b.loaded[named.Obj().Pkg().Path()] {
			return
		}
		named = named.Origin()
		for i := 0; i < named.NumMethods(); i++ {
			out[idOf(named.Method(i))] = true
		}
		return
	}
	b.dispatch(t, out, map[types.Type]bool{})
}

// dispatch adds the methods a value of type t can reach by dynamic dispatch once it is an interface value, as fmt.Sprint(v) calls
// String or Error on it, or on what it holds. Over-approximate on purpose: for a named type of the loaded packages, the whole method
// set of the type and of a pointer to it (methods promoted from embedded types included), then the type arguments, the fields and
// the elements. An interface or a type parameter adds nothing: what is behind it became an interface value where it was converted
// (the conversion rule), or is a type argument at the place of instantiation (the instance rule).
func (b *builder) dispatch(t types.Type, out map[string]bool, seen map[types.Type]bool) {
	if t == nil || seen[t] {
		return
	}
	seen[t] = true
	switch x := types.Unalias(t).(type) {
	case *types.Pointer:
		b.dispatch(x.Elem(), out, seen)
	case *types.Slice:
		b.dispatch(x.Elem(), out, seen)
	case *types.Array:
		b.dispatch(x.Elem(), out, seen)
	case *types.Chan:
		b.dispatch(x.Elem(), out, seen)
	case *types.Map:
		b.dispatch(x.Key(), out, seen)
		b.dispatch(x.Elem(), out, seen)
	case *types.Struct:
		for i := 0; i < x.NumFields(); i++ {
			b.dispatch(x.Field(i).Type(), out, seen)
		}
	case *types.Named:
		for i := 0; i < x.TypeArgs().Len(); i++ {
			b.dispatch(x.TypeArgs().At(i), out, seen)
		}
		if x.Obj().Pkg() == nil || !b.loaded[x.Obj().Pkg().Path()] {
			return // a type of another package: its methods are the business of the environment (toolchain, go.sum)
		}
		for _, tt := range []types.Type{x, types.NewPointer(x)} {
			ms := types.NewMethodSet(tt)
			for i := 0; i < ms.Len(); i++ {
				if f, ok := ms.At(i).Obj().(*types.Func); ok && f.Pkg() != nil && b.loaded[f.Pkg().Path()] {
					out[idOf(origin(f))] = true
				}
			}
		}
		b.dispatch(x.Underlying(), out, seen)
	}
}

// plantedOldMethodsOf brings back the conversion rule before dispatch: only the methods declared on a named type. A planted defect.
var plantedOldMethodsOf bool

// plantedNoTypeArgs leaves out the instance rule: a declaration that instantiates a generic function or type has no edge to the
// methods of the type arguments. A planted defect, the model before the fix of lab/testdata/go-generic-*.
var plantedNoTypeArgs bool

// noConversions switches the interface conversion rule off: a planted defect, for showing that the measurement sees it.
var noConversions bool

// plantedNoImplementers leaves out the edges from a call of an interface method (or of a method of a type parameter's constraint) to
// the methods of the types that implement it: a planted defect, for showing that the generic probe of qualify.py can fail.
var plantedNoImplementers bool

// plantedNoConstGroup switches the const group rule off (no inherited expression, no edge to the declaration): the model before
// this fix, which lab/testdata/iota showed to be unsafe. Kept so that the red case stays reproducible and the measurement keeps
// seeing it.
var plantedNoConstGroup bool

// benignReflect are the reflect functions that cannot call a method of the value they are given.
var benignReflect = map[string]bool{"DeepEqual": true, "TypeOf": true}

func appendOnce(list []string, s string) []string {
	for _, x := range list {
		if x == s {
			return list
		}
	}
	return append(list, s)
}

func (b *builder) file(pkg *packages.Package, file *ast.File) {
	path := b.fset.Position(file.Pos()).Filename
	src, err := os.ReadFile(path)
	if err != nil {
		fail("%v", err)
	}
	var fileOpaque []string
	for _, cg := range file.Comments {
		for _, c := range cg.List {
			if strings.HasPrefix(c.Text, "//go:embed") {
				fileOpaque = appendOnce(fileOpaque, "go:embed in the file")
			}
			if strings.HasPrefix(c.Text, "//go:linkname") {
				fileOpaque = appendOnce(fileOpaque, "go:linkname in the file")
			}
		}
	}
	for _, decl := range file.Decls {
		switch d := decl.(type) {
		case *ast.FuncDecl:
			obj := pkg.TypesInfo.Defs[d.Name]
			if obj == nil {
				fail("no object for %s", d.Name.Name)
			}
			kind := "func"
			if d.Recv != nil {
				kind = "method"
			}
			id := idOf(obj)
			if d.Name.Name == "init" || d.Name.Name == "_" {
				p := b.fset.Position(d.Pos())
				id = fmt.Sprintf("%s.%s@%s:%d", pkg.Types.Path(), d.Name.Name, filepath.Base(p.Filename), p.Line)
			}
			walk := []ast.Node{d.Type}
			if d.Recv != nil {
				walk = append(walk, d.Recv)
			}
			if d.Body != nil {
				walk = append(walk, d.Body)
			}
			constructs, unresolved := []string{"go.func"}, []string(nil)
			if d.Recv != nil {
				constructs = []string{"go.method"}
			}
			if d.Type.TypeParams != nil {
				constructs = append(constructs, "go.func.generic")
			}
			if d.Body == nil {
				unresolved = append(unresolved, "function without a body (assembly or linkname): its dependencies are not visible")
			}
			b.add(pkg, file, id, kind, d.Pos(), d.End(), src, walk, append([]string(nil), fileOpaque...), obj.Type().(*types.Signature), constructs, unresolved)
		case *ast.GenDecl:
			// what an implicit const repeat repeats: the type and the expressions of the last spec of this declaration that has some
			var lastType ast.Expr
			var lastValues []ast.Expr
			for _, spec := range d.Specs {
				switch s := spec.(type) {
				case *ast.TypeSpec:
					obj := pkg.TypesInfo.Defs[s.Name]
					if obj == nil {
						fail("no object for %s", s.Name.Name)
					}
					constructs := []string{"go.type"}
					if s.TypeParams != nil {
						constructs = append(constructs, "go.type.generic")
					}
					if s.Assign.IsValid() {
						constructs = append(constructs, "go.type.alias")
					}
					b.add(pkg, file, idOf(obj), "type", s.Pos(), s.End(), src, []ast.Node{s}, append([]string(nil), fileOpaque...), nil, constructs, nil)
				case *ast.ValueSpec:
					kind := "var"
					if d.Tok == token.CONST {
						kind = "const"
					}
					typ, values := s.Type, s.Values
					if d.Tok == token.CONST {
						if len(s.Values) > 0 {
							lastType, lastValues = s.Type, s.Values
						} else if !plantedNoConstGroup {
							typ, values = lastType, lastValues // the inherited expression is part of what this constant depends on
						}
					}
					walk := []ast.Node{}
					if typ != nil {
						walk = append(walk, typ)
					}
					for _, v := range values {
						walk = append(walk, v)
					}
					for _, name := range s.Names {
						if name.Name == "_" {
							continue
						}
						obj := pkg.TypesInfo.Defs[name]
						if obj == nil {
							fail("no object for %s", name.Name)
						}
						// A constant that repeats the previous spec (implicit) or uses iota has a value that depends on what else is in its
						// `const ( ... )`: the expression it inherits (walked above) and its position among the specs. The position is not
						// in the text of the spec, so the constant depends on a node for the whole declaration (constGroup).
						// (Before this was modelled, lab/testdata/iota was the known defect; -planted-no-const-group brings it back.)
						constructs := valueConstructs(pkg, d, s)
						b.add(pkg, file, idOf(obj), kind, s.Pos(), s.End(), src, walk, append([]string(nil), fileOpaque...), nil,
							constructs, nil)
						gp, ge := b.fset.Position(d.Pos()), b.fset.Position(d.End())
						gsum := sha256.Sum256(src[gp.Offset:ge.Offset])
						b.nodes[idOf(obj)].Group = hex.EncodeToString(gsum[:])
						if !plantedNoConstGroup && (contains(constructs, "go.const.implicit_repeat") || contains(constructs, "go.const.iota")) {
							gid := b.constGroup(pkg, d, src, hex.EncodeToString(gsum[:]))
							if b.deps[idOf(obj)] == nil {
								b.deps[idOf(obj)] = map[string]bool{}
							}
							b.deps[idOf(obj)][gid] = true
						}
					}
				}
			}
		}
	}
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// constGroup returns the id of the node of a const declaration as a whole, creating it once. Its source digest is that of the whole
// declaration, so the key of every constant that depends on it changes when anything in the declaration changes: conservative (an
// added constant after the last use moves no value), and sound for the value of a constant, which depends on nothing else here.
func (b *builder) constGroup(pkg *packages.Package, d *ast.GenDecl, src []byte, digest string) string {
	var first types.Object
	for _, spec := range d.Specs {
		for _, name := range spec.(*ast.ValueSpec).Names {
			if name.Name != "_" && first == nil {
				first = pkg.TypesInfo.Defs[name]
			}
		}
	}
	gid := idOf(first) + "#const-group"
	if _, ok := b.nodes[gid]; ok {
		return gid
	}
	pos, end := b.fset.Position(d.Pos()), b.fset.Position(d.End())
	rel, err := filepath.Rel(b.root, pos.Filename)
	if err != nil {
		rel = pos.Filename
	}
	b.nodes[gid] = &Node{ID: gid, Kind: "constgroup", File: rel, Line: pos.Line, EndLine: end.Line, Test: strings.HasSuffix(rel, "_test.go"),
		SrcSHA256: digest, Constructs: []string{"go.const.group"}, Analysis: Analysis{Status: "COMPLETE", Unresolved: []string{}}}
	b.deps[gid] = map[string]bool{}
	return gid
}

// valueConstructs names the constructs of one const or var spec. A const spec with no values is an implicit repeat of the previous
// spec's expression (and of its iota), recognised from the syntax of the spec itself, not from what the walk over its values found
// (that is empty, which is how the dependency was missed); a const whose values use the predeclared iota depends on its position.
func valueConstructs(pkg *packages.Package, d *ast.GenDecl, s *ast.ValueSpec) []string {
	if d.Tok != token.CONST {
		return []string{"go.var"}
	}
	out := []string{"go.const.explicit"}
	if len(s.Values) == 0 {
		out = []string{"go.const.implicit_repeat"}
	}
	iota := types.Universe.Lookup("iota")
	for _, v := range s.Values {
		ast.Inspect(v, func(nd ast.Node) bool {
			if id, ok := nd.(*ast.Ident); ok && pkg.TypesInfo.Uses[id] == iota {
				out = appendOnce(out, "go.const.iota")
			}
			return true
		})
	}
	return out
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "typeddeps: "+format+"\n", args...)
	os.Exit(1)
}

func main() {
	tests := flag.Bool("tests", true, "include the _test.go files (test variants of the packages)")
	flag.BoolVar(&plantedNoTypeArgs, "planted-no-typeargs", false, "planted defect: no edges from an instantiation to the methods of its type arguments")
	flag.BoolVar(&plantedOldMethodsOf, "planted-old-methodsof", false, "planted defect: the conversion rule looks only at methods declared on a named type")
	flag.BoolVar(&plantedNoImplementers, "planted-no-implementers", false, "planted defect: a call of an interface method has no edge to its implementers")
	flag.BoolVar(&plantedNoConstGroup, "planted-no-const-group", false, "planted defect: the model before the const group rule (implicit iota has no edge)")
	flag.BoolVar(&noConversions, "planted-no-conversions", false, "planted defect: leave out the edges of interface conversions")
	flag.Parse()
	if flag.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "usage: typeddeps [-tests=false] <dir> [<pattern> ...]")
		os.Exit(2)
	}
	root, err := filepath.Abs(flag.Arg(0))
	if err != nil {
		fail("%v", err)
	}
	patterns := flag.Args()[1:]
	if len(patterns) == 0 {
		patterns = []string{"./..."}
	}
	fset := token.NewFileSet()
	cfg := &packages.Config{Dir: root, Fset: fset, Tests: *tests,
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedSyntax | packages.NeedTypes | packages.NeedTypesInfo | packages.NeedImports | packages.NeedDeps}
	pkgs, err := packages.Load(cfg, patterns...)
	if err != nil {
		fail("load: %v", err)
	}
	if n := packages.PrintErrors(pkgs); n > 0 {
		fail("%d package error(s): the graph would be incomplete", n)
	}
	// With tests, a package comes as itself and as its test variant "p [p.test]" (a superset), plus "p_test [p.test]" and the
	// generated "p.test" main. The test variants hold every declaration of the plain package, so only they are read then.
	var use []*packages.Package
	hasVariant := map[string]bool{}
	for _, p := range pkgs {
		if strings.HasSuffix(p.ID, ".test]") && p.PkgPath == strings.Fields(p.ID)[0] {
			hasVariant[p.PkgPath] = true
		}
	}
	for _, p := range pkgs {
		if strings.HasSuffix(p.PkgPath, ".test") && p.Name == "main" {
			continue // the generated test main
		}
		if !strings.HasSuffix(p.ID, ".test]") && hasVariant[p.PkgPath] {
			continue
		}
		use = append(use, p)
	}
	b := &builder{root: root, fset: fset, loaded: map[string]bool{}, fieldOf: map[*types.Var]string{},
		nodes: map[string]*Node{}, deps: map[string]map[string]bool{}, external: map[string]bool{}}
	var paths []string
	for _, p := range use {
		b.loaded[p.PkgPath] = true
	}
	for _, p := range use {
		b.indexTypes(p)
		paths = appendOnce(paths, p.PkgPath)
	}
	for _, p := range use {
		for _, f := range p.Syntax {
			b.file(p, f)
		}
	}
	g := Graph{Schema: "cic-lab/typeddeps/v2", Scope: reportable, Nodes: []Node{}, External: []string{}}
	sort.Strings(paths)
	g.Packages = paths
	for id, n := range b.nodes {
		for d := range b.deps[id] {
			n.Deps = append(n.Deps, d)
			if _, local := b.nodes[d]; !local {
				b.external[d] = true
			}
		}
		sort.Strings(n.Deps)
		if n.Deps == nil {
			n.Deps = []string{}
		}
		sort.Strings(n.Opaque)
		g.Nodes = append(g.Nodes, *n)
	}
	sort.Slice(g.Nodes, func(i, j int) bool { return g.Nodes[i].ID < g.Nodes[j].ID })
	for e := range b.external {
		g.External = append(g.External, e)
	}
	sort.Strings(g.External)
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(g); err != nil {
		fail("%v", err)
	}
}
