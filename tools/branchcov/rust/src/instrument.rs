//! Source instrumentation for cic-branch/1 (ADR 0009). Every decision edge of the five categories is
//! enumerated from the syntax tree and given a counter; anything that cannot be enumerated is an error.

use proc_macro2::{TokenStream, TokenTree};
use quote::quote;
use serde::Serialize;
use syn::spanned::Spanned;
use syn::visit::Visit;
use syn::visit_mut::{self, VisitMut};
use syn::{parse_quote, Attribute, BinOp, Expr, ExprIf, Item, Stmt};

#[derive(Serialize, Clone)]
pub struct Edge {
    pub id: usize,
    pub file: String,
    pub line: usize,
    pub col: usize,
    pub category: &'static str,
    pub kind: &'static str,
    pub label: &'static str,
}

pub struct Instr<'a> {
    pub file: String,
    pub edges: &'a mut Vec<Edge>,
    pub next: &'a mut usize,
    pub errs: Vec<String>,
    iters: usize,
}

impl<'a> Instr<'a> {
    pub fn new(file: &str, edges: &'a mut Vec<Edge>, next: &'a mut usize) -> Self {
        Instr { file: file.to_string(), edges, next, errs: vec![], iters: 0 }
    }

    fn fail(&mut self, span: proc_macro2::Span, msg: &str) {
        let s = span.start();
        self.errs.push(format!("{}:{}:{}: {}", self.file, s.line, s.column + 1, msg));
    }

    fn edge(&mut self, span: proc_macro2::Span, category: &'static str, kind: &'static str, label: &'static str) -> usize {
        let s = span.start();
        let id = *self.next;
        *self.next += 1;
        self.edges.push(Edge { id, file: self.file.clone(), line: s.line, col: s.column + 1, category, kind, label });
        id
    }

    /// Two edges (true, false) for a boolean expression; returns the id of the true edge.
    fn pair(&mut self, span: proc_macro2::Span, category: &'static str, kind: &'static str, t: &'static str, f: &'static str) -> usize {
        let id = self.edge(span, category, kind, t);
        self.edge(span, category, kind, f);
        id
    }

    fn wrap_cond(&mut self, span: proc_macro2::Span, e: &mut Expr, category: &'static str, kind: &'static str, t: &'static str, f: &'static str) {
        let id = self.pair(span, category, kind, t, f);
        let inner = std::mem::replace(e, Expr::Verbatim(TokenStream::new()));
        *e = parse_quote!(crate::rustbranch_rt::cond(#id, #inner));
    }

    fn hit(id: usize) -> Stmt {
        parse_quote!(crate::rustbranch_rt::hit(#id);)
    }

    /// The operands of a && / || chain.
    fn chain(&mut self, e: &mut Expr) {
        match e {
            Expr::Binary(b) if matches!(b.op, BinOp::And(_) | BinOp::Or(_)) => {
                self.chain(&mut b.left);
                self.chain(&mut b.right);
            }
            Expr::Paren(p) => self.chain(&mut p.expr),
            Expr::Let(l) => self.fail(l.span(), "let chains are not supported: the operands cannot be enumerated"),
            leaf => {
                let span = leaf.span();
                self.visit_expr_mut(leaf);
                self.wrap_cond(span, leaf, "short_circuit", "operand", "true", "false");
            }
        }
    }

    fn check_macro(&mut self, mac: &syn::Macro) {
        if let Some(t) = decision_token(mac.tokens.clone()) {
            self.fail(mac.span(), &format!("`{}` inside a macro invocation cannot be enumerated", t));
        }
    }

    fn check_attrs(&mut self, attrs: &[Attribute]) {
        for a in attrs {
            if a.path().is_ident("cfg") || a.path().is_ident("cfg_attr") {
                self.fail(a.span(), "conditional compilation is not supported: the measured build must equal the scope");
            }
        }
    }
}

fn is_cfg_test(a: &Attribute) -> bool {
    a.path().is_ident("test") || (a.path().is_ident("cfg") && quote!(#a).to_string().replace(' ', "") == "#[cfg(test)]")
}

/// The first token that would hide a decision inside a macro invocation.
fn decision_token(ts: TokenStream) -> Option<String> {
    let toks: Vec<TokenTree> = ts.into_iter().collect();
    for (i, t) in toks.iter().enumerate() {
        match t {
            TokenTree::Ident(id) => {
                let s = id.to_string();
                if ["if", "match", "while", "for", "loop"].contains(&s.as_str()) {
                    return Some(s);
                }
            }
            TokenTree::Punct(p) => {
                let c = p.as_char();
                if c == '?' {
                    return Some("?".into());
                }
                if (c == '&' || c == '|') && p.spacing() == proc_macro2::Spacing::Joint {
                    if let Some(TokenTree::Punct(q)) = toks.get(i + 1) {
                        if q.as_char() == c {
                            return Some(format!("{}{}", c, c));
                        }
                    }
                }
            }
            TokenTree::Group(g) => {
                if let Some(s) = decision_token(g.stream()) {
                    return Some(s);
                }
            }
            _ => {}
        }
    }
    None
}

/// Does a node contain a decision construct (used for const contexts, which cannot be instrumented)?
/// `strict` also counts && and ||: in a const fn they are runtime decisions, in a const initializer they are
/// compile-time constants (like `const c = a && b` in the Go sample, they stay untouched).
struct Detector {
    found: bool,
    strict: bool,
}
impl<'ast> Visit<'ast> for Detector {
    fn visit_expr(&mut self, e: &'ast Expr) {
        match e {
            Expr::If(_) | Expr::Match(_) | Expr::While(_) | Expr::ForLoop(_) | Expr::Try(_) => self.found = true,
            Expr::Binary(b) if self.strict && matches!(b.op, BinOp::And(_) | BinOp::Or(_)) => self.found = true,
            _ => {}
        }
        syn::visit::visit_expr(self, e);
    }
}

fn has_decision(item: &Item, strict: bool) -> bool {
    let mut d = Detector { found: false, strict };
    d.visit_item(item);
    d.found
}

impl<'a> VisitMut for Instr<'a> {
    fn visit_item_mut(&mut self, item: &mut Item) {
        let attrs: Vec<Attribute> = match item {
            Item::Fn(f) => f.attrs.clone(),
            Item::Mod(m) => m.attrs.clone(),
            Item::Impl(i) => i.attrs.clone(),
            Item::Const(c) => c.attrs.clone(),
            Item::Static(s) => s.attrs.clone(),
            Item::Trait(t) => t.attrs.clone(),
            Item::Use(u) => u.attrs.clone(),
            _ => vec![],
        };
        if attrs.iter().any(is_cfg_test) {
            // test code is not own source: it is dropped from the measured copy, not enumerated
            *item = Item::Verbatim(TokenStream::new());
            return;
        }
        self.check_attrs(&attrs);
        match item {
            Item::Const(_) | Item::Static(_) => {
                if has_decision(item, false) {
                    self.fail(item.span(), "a decision inside a const or static initializer cannot be measured");
                }
            }
            Item::Fn(f) if f.sig.constness.is_some() => {
                if has_decision(item, true) {
                    self.fail(item.span(), "a decision inside a const fn cannot be measured");
                }
            }
            Item::Macro(m) => {
                if let Some(t) = decision_token(m.mac.tokens.clone()) {
                    self.fail(m.span(), &format!("`{}` inside a macro definition or invocation cannot be enumerated", t));
                }
            }
            _ => visit_mut::visit_item_mut(self, item),
        }
    }

    fn visit_impl_item_fn_mut(&mut self, f: &mut syn::ImplItemFn) {
        self.check_attrs(&f.attrs);
        if f.sig.constness.is_some() {
            let mut d = Detector { found: false, strict: true };
            d.visit_block(&f.block);
            if d.found {
                self.fail(f.span(), "a decision inside a const fn cannot be measured");
            }
            return;
        }
        visit_mut::visit_impl_item_fn_mut(self, f);
    }

    fn visit_stmt_mut(&mut self, s: &mut Stmt) {
        if let Stmt::Local(l) = s {
            if l.init.as_ref().map_or(false, |i| i.diverge.is_some()) {
                self.fail(l.span(), "let-else is a decision that cic-branch/1 does not define; it cannot be measured");
            }
            self.check_attrs(&l.attrs);
        }
        if let Stmt::Macro(m) = s {
            self.check_macro(&m.mac);
        }
        visit_mut::visit_stmt_mut(self, s);
    }

    fn visit_expr_mut(&mut self, e: &mut Expr) {
        match e {
            Expr::Macro(m) => {
                self.check_macro(&m.mac);
            }
            Expr::Binary(b) if matches!(b.op, BinOp::And(_) | BinOp::Or(_)) => {
                self.chain(e);
            }
            Expr::If(i) => self.visit_if(i),
            Expr::While(w) => {
                let is_let = matches!(*w.cond, Expr::Let(_));
                if is_let {
                    let span = w.span();
                    let (pat, mut scrut) = match &*w.cond {
                        Expr::Let(l) => ((*l.pat).clone(), (*l.expr).clone()),
                        _ => unreachable!(),
                    };
                    self.visit_expr_mut(&mut scrut);
                    self.visit_block_mut(&mut w.body);
                    let it = self.edge(span, "loop", "while-let", "iterate");
                    let ex = self.edge(span, "loop", "while-let", "exit");
                    let stmts = std::mem::take(&mut w.body.stmts);
                    let hit_it = Self::hit(it);
                    let hit_ex = Self::hit(ex);
                    let label = w.label.clone();
                    let attrs = w.attrs.clone();
                    let body: syn::Block = parse_quote!({
                        match #scrut {
                            #pat => { #hit_it #(#stmts)* }
                            #[allow(unreachable_patterns)]
                            _ => { #hit_ex break; }
                        }
                    });
                    *e = Expr::Loop(syn::ExprLoop { attrs, label, loop_token: Default::default(), body });
                } else {
                    let span = w.cond.span();
                    self.visit_expr_mut(&mut w.cond);
                    self.wrap_cond(span, &mut w.cond, "loop", "while", "iterate", "exit");
                    self.visit_block_mut(&mut w.body);
                }
            }
            Expr::ForLoop(f) => {
                let span = f.span();
                self.visit_expr_mut(&mut f.expr);
                self.visit_block_mut(&mut f.body);
                let it = self.edge(span, "loop", "for", "iterate");
                let ex = self.edge(span, "loop", "for", "exit");
                self.iters += 1;
                let var = syn::Ident::new(&format!("__rb_it{}", self.iters), proc_macro2::Span::call_site());
                let pat = (*f.pat).clone();
                let iter = (*f.expr).clone();
                let stmts = std::mem::take(&mut f.body.stmts);
                let hit_it = Self::hit(it);
                let hit_ex = Self::hit(ex);
                let label = f.label.clone();
                let attrs = f.attrs.clone();
                let body: syn::Block = parse_quote!({
                    match ::core::iter::Iterator::next(&mut #var) {
                        ::core::option::Option::Some(#pat) => { #hit_it #(#stmts)* }
                        ::core::option::Option::None => { #hit_ex break; }
                    }
                });
                let lp = Expr::Loop(syn::ExprLoop { attrs, label, loop_token: Default::default(), body });
                *e = parse_quote!({
                    let mut #var = ::core::iter::IntoIterator::into_iter(#iter);
                    #lp
                });
            }
            Expr::Match(m) => {
                self.visit_expr_mut(&mut m.expr);
                for arm in m.arms.iter_mut() {
                    self.check_attrs(&arm.attrs);
                    let arm_span = arm.pat.span();
                    if let Some((_, g)) = arm.guard.as_mut() {
                        let gs = g.span();
                        self.visit_expr_mut(g);
                        self.wrap_cond(gs, g, "match", "guard", "true", "false");
                    }
                    self.visit_expr_mut(&mut arm.body);
                    let id = self.edge(arm_span, "match", "match", "arm");
                    let body = std::mem::replace(&mut *arm.body, Expr::Verbatim(TokenStream::new()));
                    *arm.body = parse_quote!({ crate::rustbranch_rt::hit(#id); #body });
                }
            }
            Expr::Try(t) => {
                let span = t.question_token.spans[0];
                self.visit_expr_mut(&mut t.expr);
                let id = self.pair(span, "error_path", "try", "continue", "propagate");
                let inner = std::mem::replace(&mut *t.expr, Expr::Verbatim(TokenStream::new()));
                *t.expr = parse_quote!(crate::rustbranch_rt::q(#id, #inner));
            }
            _ => visit_mut::visit_expr_mut(self, e),
        }
    }
}

impl<'a> Instr<'a> {
    fn visit_if(&mut self, i: &mut ExprIf) {
        if contains_let(&i.cond) && !matches!(*i.cond, Expr::Let(_)) {
            self.fail(i.span(), "let chains are not supported: the operands cannot be enumerated");
            return;
        }
        if matches!(*i.cond, Expr::Let(_)) {
            let span = i.span();
            if let Expr::Let(l) = &mut *i.cond {
                self.visit_expr_mut(&mut l.expr);
            }
            let t = self.edge(span, "conditional", "if-let", "matched");
            let f = self.edge(span, "conditional", "if-let", "not-matched");
            self.visit_block_mut(&mut i.then_branch);
            i.then_branch.stmts.insert(0, Self::hit(t));
            let hit_f = Self::hit(f);
            match i.else_branch.take() {
                Some((tok, mut els)) => {
                    self.visit_expr_mut(&mut els);
                    let blk: Expr = parse_quote!({ #hit_f #els });
                    i.else_branch = Some((tok, Box::new(blk)));
                }
                None => {
                    let blk: Expr = parse_quote!({ #hit_f });
                    i.else_branch = Some((Default::default(), Box::new(blk)));
                }
            }
            return;
        }
        let span = i.cond.span();
        self.visit_expr_mut(&mut i.cond);
        self.wrap_cond(span, &mut i.cond, "conditional", "if", "true", "false");
        self.visit_block_mut(&mut i.then_branch);
        if let Some((_, els)) = i.else_branch.as_mut() {
            self.visit_expr_mut(els);
        }
    }
}

fn contains_let(e: &Expr) -> bool {
    match e {
        Expr::Let(_) => true,
        Expr::Binary(b) => contains_let(&b.left) || contains_let(&b.right),
        Expr::Paren(p) => contains_let(&p.expr),
        _ => false,
    }
}

/// Instrument one source file; returns the new source text.
pub fn instrument(file: &str, src: &str, next: &mut usize, edges: &mut Vec<Edge>) -> Result<String, String> {
    let mut ast = syn::parse_file(src).map_err(|e| format!("{}: {}", file, e))?;
    let mut inst = Instr::new(file, edges, next);
    inst.check_attrs(&ast.attrs.iter().filter(|a| !a.path().is_ident("forbid") && !a.path().is_ident("deny")).cloned().collect::<Vec<_>>());
    for item in ast.items.iter_mut() {
        inst.visit_item_mut(item);
    }
    if !inst.errs.is_empty() {
        return Err(inst.errs.join("\n"));
    }
    ast.items.retain(|i| !matches!(i, Item::Verbatim(t) if t.is_empty()));
    Ok(prettyplease::unparse(&ast))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(src: &str) -> Result<(Vec<String>, String), String> {
        let (mut next, mut edges) = (0, vec![]);
        let out = instrument("x.rs", src, &mut next, &mut edges)?;
        Ok((edges.iter().map(|e| format!("{}/{}/{}", e.category, e.kind, e.label)).collect(), out))
    }

    fn edges(src: &str) -> String {
        run(src).unwrap().0.join(" ")
    }

    #[test]
    fn enumeration() {
        let t = "short_circuit/operand/true short_circuit/operand/false";
        let cases: Vec<(&str, String)> = vec![
            ("fn f(x: i32) { if x > 0 {} }", "conditional/if/true conditional/if/false".into()),
            ("fn f(x: i32) { if x > 0 {} else if x < 0 {} else {} }", "conditional/if/true conditional/if/false conditional/if/true conditional/if/false".into()),
            ("fn f(o: Option<i32>) { if let Some(_) = o {} }", "conditional/if-let/matched conditional/if-let/not-matched".into()),
            ("fn f(a: bool, b: bool, c: bool) -> bool { a && b || c }", format!("{t} {t} {t}")),
            ("fn f(a: bool, b: bool) -> bool { !(a && b) }", format!("{t} {t}")),
            ("fn f(n: i32) { while n > 0 {} }", "loop/while/iterate loop/while/exit".into()),
            ("fn f(mut v: Vec<i32>) { while let Some(_) = v.pop() {} }", "loop/while-let/iterate loop/while-let/exit".into()),
            ("fn f(v: &[i32]) { for _ in v {} }", "loop/for/iterate loop/for/exit".into()),
            ("fn f() { loop { break; } }", "".into()),
            ("fn f(n: i32) { match n { 0 => {} x if x > 1 => {} _ => {} } }", "match/match/arm match/guard/true match/guard/false match/match/arm match/match/arm".into()),
            ("fn f(s: &str) -> Result<i32, std::num::ParseIntError> { Ok(s.parse::<i32>()?) }", "error_path/try/continue error_path/try/propagate".into()),
            ("fn f() -> impl Fn(bool, bool) -> bool { |a, b| a || b }", format!("{t} {t}")),
            ("#[cfg(test)] mod tests { fn t(a: bool, b: bool) -> bool { a && b } }", "".into()),
            ("#[test] fn t(a: bool) { if a {} }", "".into()),
            ("const C: bool = true && false;", "".into()),
        ];
        for (src, want) in cases {
            assert_eq!(edges(src), want, "{}", src);
        }
    }

    #[test]
    fn output_parses() {
        let srcs = [
            "fn f(m: &[Vec<i32>]) { 'o: for r in m { for v in r { if *v > 0 { break 'o; } } } }",
            "fn f(mut v: Vec<i32>) { 'o: while let Some(x) = v.pop() { if x > 0 { continue 'o; } } }",
            "fn f(n: i32) -> i32 { match n { 0 => return 1, x if x > 3 => x, _ => 0 } }",
            "fn f(o: Option<i32>) -> Option<i32> { let v = o?; Some(v) }",
        ];
        for s in srcs {
            let (_, out) = run(s).unwrap();
            syn::parse_file(&out).unwrap_or_else(|e| panic!("{}: {}\n{}", s, e, out));
        }
    }

    #[test]
    fn refusals() {
        let cases = [
            ("let-else", "fn f(o: Option<i32>) { let Some(_) = o else { return }; }"),
            ("let chain", "fn f(o: Option<i32>, b: bool) { if let Some(_) = o && b {} }"),
            ("condition in a macro", "fn f(a: bool, b: bool) { assert!(a && b); }"),
            ("question mark in a macro", "fn f() -> Option<i32> { Some(vec![Some(1)?]) .map(|_| 1) }"),
            ("if in a macro definition", "macro_rules! m { ($x:expr) => { if $x { 1 } else { 2 } } }"),
            ("cfg", "#[cfg(unix)] fn f(a: bool) { if a {} }"),
            ("cfg_attr", "#[cfg_attr(unix, inline)] fn f(a: bool) { if a {} }"),
            ("decision in a const fn", "const fn f(a: bool) -> i32 { if a { 1 } else { 2 } }"),
            ("decision in a const", "const C: i32 = if true { 1 } else { 2 };"),
        ];
        for (name, src) in cases {
            assert!(run(src).is_err(), "{} must be refused", name);
        }
    }

    #[test]
    fn instrumented_for_loop_keeps_the_label_and_value_semantics() {
        let (_, out) = run("fn f(v: &[i32]) -> i32 { let mut s = 0; 'a: for x in v { if *x < 0 { break 'a; } s += x; } s }").unwrap();
        assert!(out.contains("'a: loop"), "{}", out);
    }
}
