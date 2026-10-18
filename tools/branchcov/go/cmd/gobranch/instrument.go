package main

import (
	"bytes"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"sort"
	"strconv"
	"strings"
)

// Edge is one possible outgoing edge of a control-flow decision (cic-branch/1, ADR 0009).
type Edge struct {
	ID       int    `json:"id"`
	File     string `json:"file"`
	Line     int    `json:"line"`
	Col      int    `json:"col"`
	Category string `json:"category"` // conditional | match | loop | short_circuit | error_path
	Kind     string `json:"kind"`     // if | switch | type-switch | select | for | range | operand
	Label    string `json:"label"`    // true | false | case | default | no-match | iterate | exit
}

// ctx is an enclosing statement that a break can target.
type ctx struct {
	label string
	flag  string // set for a range loop: the variable recording that it was left by a break
}

type instr struct {
	fset  *token.FileSet
	file  string
	next  *int
	edges *[]Edge
	seen  map[ast.Node]bool
	stack []ctx
	flags int
	errs  []string
}

func (in *instr) fail(pos token.Pos, format string, a ...interface{}) {
	p := in.fset.Position(pos)
	in.errs = append(in.errs, fmt.Sprintf("%s:%d:%d: %s", in.file, p.Line, p.Column, fmt.Sprintf(format, a...)))
}

func (in *instr) edge(pos token.Pos, category, kind, label string) int {
	p := in.fset.Position(pos)
	id := *in.next
	*in.next++
	*in.edges = append(*in.edges, Edge{ID: id, File: in.file, Line: p.Line, Col: p.Column, Category: category, Kind: kind, Label: label})
	return id
}

func call(name string, args ...ast.Expr) *ast.CallExpr {
	return &ast.CallExpr{Fun: ast.NewIdent(name), Args: args}
}

func intLit(n int) ast.Expr { return &ast.BasicLit{Kind: token.INT, Value: strconv.Itoa(n)} }

func (in *instr) hit(id int) ast.Stmt { return &ast.ExprStmt{X: call("gobranchHit", intLit(id))} }

// wrapCond records both outcomes of a boolean expression: the true edge gets id, the false edge id+1.
func (in *instr) wrapCond(e ast.Expr, category, kind, trueLabel, falseLabel string) ast.Expr {
	t := in.edge(e.Pos(), category, kind, trueLabel)
	in.edge(e.Pos(), category, kind, falseLabel)
	return call("gobranchCond", intLit(t), e)
}

func isTrue(e ast.Expr) bool {
	for {
		p, ok := e.(*ast.ParenExpr)
		if !ok {
			break
		}
		e = p.X
	}
	id, ok := e.(*ast.Ident)
	return ok && id.Name == "true"
}

// wrapLeaves wraps every operand of a && / || chain, keeping the chain itself.
func (in *instr) wrapLeaves(e ast.Expr) ast.Expr {
	switch x := e.(type) {
	case *ast.ParenExpr:
		x.X = in.wrapLeaves(x.X)
		return x
	case *ast.BinaryExpr:
		if x.Op == token.LAND || x.Op == token.LOR {
			in.seen[x] = true
			x.X = in.wrapLeaves(x.X)
			x.Y = in.wrapLeaves(x.Y)
			return x
		}
	}
	return in.wrapCond(e, "short_circuit", "operand", "true", "false")
}

// exprWalk instruments the boolean operators and the function literals inside a node that holds no
// structural statement of its own.
func (in *instr) exprWalk(n ast.Node) {
	if n == nil {
		return
	}
	ast.Inspect(n, func(c ast.Node) bool {
		switch x := c.(type) {
		case *ast.FuncLit:
			saved := in.stack
			in.stack = nil
			x.Body = in.rewriteBlock(x.Body)
			in.stack = saved
			return false
		case *ast.BinaryExpr:
			if (x.Op == token.LAND || x.Op == token.LOR) && !in.seen[x] {
				in.wrapLeaves(x)
			}
		}
		return true
	})
}

func (in *instr) rewriteBlock(b *ast.BlockStmt) *ast.BlockStmt {
	if b == nil {
		return nil
	}
	b.List = in.rewriteList(b.List)
	return b
}

func (in *instr) rewriteList(list []ast.Stmt) []ast.Stmt {
	out := make([]ast.Stmt, 0, len(list))
	for _, s := range list {
		out = append(out, in.rewriteStmt(s, ""))
	}
	return out
}

func (in *instr) push(label, flag string) { in.stack = append(in.stack, ctx{label, flag}) }
func (in *instr) pop()                    { in.stack = in.stack[:len(in.stack)-1] }

func (in *instr) rewriteStmt(s ast.Stmt, label string) ast.Stmt {
	switch x := s.(type) {
	case *ast.BlockStmt:
		return in.rewriteBlock(x)
	case *ast.LabeledStmt:
		x.Stmt = in.rewriteStmt(x.Stmt, x.Label.Name)
		if blk, ok := x.Stmt.(*ast.BlockStmt); ok && isRangeWrapper(blk) {
			// The label must stay on the loop itself (continue L), so it moves inside the wrapper.
			loop := blk.List[1]
			blk.List[1] = &ast.LabeledStmt{Label: x.Label, Stmt: loop}
			return blk
		}
		return x
	case *ast.IfStmt:
		if x.Init != nil {
			in.exprWalk(x.Init)
		}
		x.Cond = in.wrapCond(x.Cond, "conditional", "if", "true", "false")
		in.exprWalk(x.Cond)
		x.Body = in.rewriteBlock(x.Body)
		if x.Else != nil {
			x.Else = in.rewriteStmt(x.Else, "")
		}
		return x
	case *ast.ForStmt:
		if x.Init != nil {
			in.exprWalk(x.Init)
		}
		if x.Post != nil {
			in.exprWalk(x.Post)
		}
		if x.Cond != nil {
			if !isTrue(x.Cond) {
				x.Cond = in.wrapCond(x.Cond, "loop", "for", "iterate", "exit")
			}
			in.exprWalk(x.Cond)
		}
		in.push(label, "")
		x.Body = in.rewriteBlock(x.Body)
		in.pop()
		return x
	case *ast.RangeStmt:
		return in.rewriteRange(x, label)
	case *ast.SwitchStmt:
		if x.Init != nil {
			in.exprWalk(x.Init)
		}
		if x.Tag != nil {
			in.exprWalk(x.Tag)
		}
		in.rewriteCases(x.Body, x.Pos(), "switch", label)
		return x
	case *ast.TypeSwitchStmt:
		if x.Init != nil {
			in.exprWalk(x.Init)
		}
		in.exprWalk(x.Assign)
		in.rewriteCases(x.Body, x.Pos(), "type-switch", label)
		return x
	case *ast.SelectStmt:
		in.push(label, "")
		for _, c := range x.Body.List {
			cc := c.(*ast.CommClause)
			lab := "case"
			if cc.Comm == nil {
				lab = "default"
			} else {
				in.exprWalk(cc.Comm)
			}
			id := in.edge(cc.Pos(), "match", "select", lab)
			cc.Body = append([]ast.Stmt{in.hit(id)}, in.rewriteList(cc.Body)...)
		}
		in.pop()
		return x
	case *ast.BranchStmt:
		if x.Tok == token.GOTO {
			in.fail(x.Pos(), "goto is not supported: the control flow cannot be enumerated")
			return x
		}
		if x.Tok == token.BREAK {
			if t := in.target(x); t != nil && t.flag != "" {
				return &ast.BlockStmt{List: []ast.Stmt{
					&ast.AssignStmt{Lhs: []ast.Expr{ast.NewIdent(t.flag)}, Tok: token.ASSIGN, Rhs: []ast.Expr{ast.NewIdent("true")}},
					x,
				}}
			}
		}
		return x
	case *ast.DeclStmt:
		if g, ok := x.Decl.(*ast.GenDecl); ok && g.Tok == token.CONST {
			return x // constant expressions stay constant
		}
		in.exprWalk(x)
		return x
	default:
		in.exprWalk(s)
		return s
	}
}

func isRangeWrapper(b *ast.BlockStmt) bool {
	if len(b.List) != 3 {
		return false
	}
	d, ok := b.List[0].(*ast.DeclStmt)
	if !ok {
		return false
	}
	g, ok := d.Decl.(*ast.GenDecl)
	if !ok || len(g.Specs) != 1 {
		return false
	}
	vs, ok := g.Specs[0].(*ast.ValueSpec)
	return ok && len(vs.Names) == 1 && strings.HasPrefix(vs.Names[0].Name, "gobranchBrk")
}

func (in *instr) target(b *ast.BranchStmt) *ctx {
	if b.Label != nil {
		for i := len(in.stack) - 1; i >= 0; i-- {
			if in.stack[i].label == b.Label.Name {
				return &in.stack[i]
			}
		}
		return nil
	}
	if len(in.stack) == 0 {
		return nil
	}
	return &in.stack[len(in.stack)-1]
}

func (in *instr) rewriteRange(x *ast.RangeStmt, label string) ast.Stmt {
	in.exprWalk(x.X)
	in.flags++
	flag := fmt.Sprintf("gobranchBrk%d", in.flags)
	iter := in.edge(x.Pos(), "loop", "range", "iterate")
	exit := in.edge(x.Pos(), "loop", "range", "exit")
	in.push(label, flag)
	body := in.rewriteList(x.Body.List)
	in.pop()
	x.Body.List = append([]ast.Stmt{in.hit(iter)}, body...)
	return &ast.BlockStmt{List: []ast.Stmt{
		&ast.DeclStmt{Decl: &ast.GenDecl{Tok: token.VAR, Specs: []ast.Spec{
			&ast.ValueSpec{Names: []*ast.Ident{ast.NewIdent(flag)}, Type: ast.NewIdent("bool")}}}},
		x,
		&ast.IfStmt{
			Cond: &ast.UnaryExpr{Op: token.NOT, X: ast.NewIdent(flag)},
			Body: &ast.BlockStmt{List: []ast.Stmt{in.hit(exit)}},
		},
	}}
}

// rewriteCases instruments the clauses of a switch or type switch: one edge per clause, plus the
// implicit no-match edge when there is no default.
func (in *instr) rewriteCases(body *ast.BlockStmt, pos token.Pos, kind, label string) {
	in.push(label, "")
	hasDefault := false
	for _, c := range body.List {
		cc := c.(*ast.CaseClause)
		lab := "case"
		if cc.List == nil {
			hasDefault = true
			lab = "default"
		}
		for _, e := range cc.List {
			in.exprWalk(e)
		}
		id := in.edge(cc.Pos(), "match", kind, lab)
		cc.Body = append([]ast.Stmt{in.hit(id)}, in.rewriteList(cc.Body)...)
	}
	in.pop()
	if hasDefault {
		return
	}
	if n := len(body.List); n > 0 {
		last := body.List[n-1].(*ast.CaseClause)
		if l := len(last.Body); l > 0 {
			if b, ok := last.Body[l-1].(*ast.BranchStmt); ok && b.Tok == token.FALLTHROUGH {
				in.fail(b.Pos(), "fallthrough in the last clause of a switch without default would run into the no-match edge")
			}
		}
	}
	id := in.edge(pos, "match", kind, "no-match")
	body.List = append(body.List, &ast.CaseClause{Body: []ast.Stmt{in.hit(id)}})
}

// Instrument rewrites one Go source file. next and edges are shared by the files of a package.
func Instrument(fset *token.FileSet, name string, src []byte, next *int, edges *[]Edge) ([]byte, error) {
	f, err := parser.ParseFile(fset, name, src, parser.SkipObjectResolution)
	if err != nil {
		return nil, err
	}
	in := &instr{fset: fset, file: name, next: next, edges: edges, seen: map[ast.Node]bool{}}
	for _, d := range f.Decls {
		switch x := d.(type) {
		case *ast.FuncDecl:
			if x.Body != nil {
				in.stack = nil
				x.Body = in.rewriteBlock(x.Body)
			}
		case *ast.GenDecl:
			if x.Tok == token.CONST {
				continue
			}
			in.exprWalk(x)
		}
	}
	if len(in.errs) > 0 {
		sort.Strings(in.errs)
		return nil, fmt.Errorf("%s", strings.Join(in.errs, "\n"))
	}
	var buf bytes.Buffer
	if err := printer.Fprint(&buf, fset, f); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
