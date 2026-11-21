"""Plants four defects in the surface-level listing, one at a time, in Go and in Rust, and requires the shared-vector test of THAT language to fail
with the expected needle. Usage: seeded_faults.py <forms checkout> ; env GO and CARGO name the commands (container stand-ins are fine).
The tree is restored after every run. A fault that does not turn the test red, or turns it red for another reason, fails this tool."""
import os, shutil, subprocess, sys
root = sys.argv[1]
GO = os.environ.get("GO", "go").split(); CARGO = os.environ.get("CARGO", "cargo").split()
def sub(p, a, b):
    """Return the text of file `p` with the one occurrence of `a` replaced by `b`; fails when `a` does not occur exactly once."""
    t = open(p, encoding="utf-8").read(); assert t.count(a) == 1, (p, a); return t.replace(a, b, 1)
FAULTS = [
 ("F1 nothing is listed", [
   ("go/schema.go", '\tv := lookup(surface, "validation")\n\tif v == nil {', '\tv := lookup(surface, "validation")\n\tif v == nil || true {'),
   ], [("rust/src/schema.rs", '        None => Vec::new(),\n        Some(v) =>', '        _ if true => Vec::new(),\n        None => Vec::new(),\n        Some(v) =>')]),
 ("F2 index off by one", [
   ("go/schema.go", 'sprintf("%s.validation[%d]", name, i)', 'sprintf("%s.validation[%d]", name, i+1)')],
  [("rust/src/schema.rs", 'format!("{name}.validation[{i}]")', 'format!("{name}.validation[{}]", i + 1)')]),
 ("F3 a member that is not a list is not listed", [
   ("go/schema.go", '\t\treturn []string{name + ".validation"}', '\t\treturn nil')],
  [("rust/src/schema.rs", 'None => vec![format!("{name}.validation")],', 'None => Vec::new(),')]),
 ("F4 the entries are merged into the node list instead of listed apart", [
   ("go/schema.go", 'out.UnevaluatedSurfaceContracts = surfaceValidation(surface, in.Surface)', 'out.UnevaluatedContracts = append(out.UnevaluatedContracts, surfaceValidation(surface, in.Surface)...)')],
  [("rust/src/schema.rs", 'unevaluated_surface_contracts: surface_validation(surface, input.surface),', 'unevaluated_surface_contracts: Vec::new(),')]),
]
def run(lang):
    """Run the shared-vector schema test of one language (`go` or `rust`) in the checkout and return the finished process."""
    if lang == "go":
        return subprocess.run(GO + ["test", "-run", "TestSharedSchemaCases", "."], cwd=root + "/go", capture_output=True, text=True)
    return subprocess.run(CARGO + ["test", "--test", "schema"], cwd=root + "/rust", capture_output=True, text=True)
base = {l: run(l) for l in ("go", "rust")}
for l, r in base.items():
    print("baseline %s: rc=%d" % (l, r.returncode)); assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
bad = 0
for name, gofix, rsfix in FAULTS:
    for lang, edits in (("go", gofix), ("rust", rsfix)):
        saved = {}
        try:
            for p, a, b in edits:
                fp = os.path.join(root, p); saved.setdefault(fp, open(fp, encoding="utf-8").read())
                new = sub(fp, a, b)  # read and compute BEFORE the file is opened for writing
                open(fp, "w", encoding="utf-8").write(new)
            r = run(lang); out = r.stdout + r.stderr
            red = r.returncode != 0 and "surface-validation." in out  # the failing case is one of the new vectors
            built = "error[" not in out and "cannot find" not in out and "undefined:" not in out and "declared and not used" not in out
            ok = red and built
            print("%-5s %-60s %s" % (lang, name, "RED as expected" if ok else "NOT RED / does not build"))
            bad += 0 if ok else 1
        finally:
            for fp, t in saved.items(): open(fp, "w", encoding="utf-8").write(t)
sys.exit(1 if bad else 0)
