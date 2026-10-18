// Command gobranch measures cic-branch/1 branch coverage of a Go module (ADR 0009).
//
//	gobranch run -C DIR -o OUT.json [-- go test flags]
//
// It enumerates every edge of the control-flow decisions of the own (non-test) source files of the packages of DIR from
// the AST, instruments a copy of the files through the Go overlay mechanism, runs `go test` and writes, per edge, how
// often it was taken. Anything it cannot enumerate or instrument is an error: there is no partial result.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

const toolName, toolVersion = "gobranch", "0.1.0"

type listPkg struct {
	Dir            string
	ImportPath     string
	Name           string
	GoFiles        []string
	CgoFiles       []string
	IgnoredGoFiles []string
	EmbedFiles     []string
	TestGoFiles    []string
	XTestGoFiles   []string
	Module         *struct {
		Path      string
		Dir       string
		GoVersion string
	}
}

type fileResult struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Edges  []Edge `json:"edges"`
	Hits   []int  `json:"hits"`
}

type pkgResult struct {
	ImportPath string       `json:"import_path"`
	Files      []fileResult `json:"files"`
}

type result struct {
	Tool         map[string]string `json:"tool"`
	GoVersion    string            `json:"go_version"`
	Packages     []pkgResult       `json:"packages"`
	IgnoredFiles []string          `json:"ignored_files"`
}

func die(format string, a ...interface{}) {
	fmt.Fprintf(os.Stderr, "gobranch: "+format+"\n", a...)
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 || os.Args[1] != "run" {
		die("usage: gobranch run -C DIR -o OUT.json [-- go test flags]")
	}
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	dir := fs.String("C", ".", "module directory")
	out := fs.String("o", "", "output file")
	fs.Parse(os.Args[2:])
	if *out == "" {
		die("-o is required")
	}
	root, err := filepath.Abs(*dir)
	if err != nil {
		die("%v", err)
	}
	res, err := run(root, fs.Args())
	if err != nil {
		die("%v", err)
	}
	b, _ := json.MarshalIndent(res, "", " ")
	if err := os.WriteFile(*out, append(b, '\n'), 0o644); err != nil {
		die("%v", err)
	}
}

func goCmd(root string, env []string, args ...string) *exec.Cmd {
	c := exec.Command("go", args...)
	c.Dir = root
	c.Env = append(os.Environ(), env...)
	return c
}

func listPackages(root string) ([]listPkg, error) {
	c := goCmd(root, nil, "list", "-json", "./...")
	var stderr strings.Builder
	c.Stderr = &stderr
	b, err := c.Output()
	if err != nil {
		return nil, fmt.Errorf("go list: %v\n%s", err, stderr.String())
	}
	var pkgs []listPkg
	dec := json.NewDecoder(strings.NewReader(string(b)))
	for dec.More() {
		var p listPkg
		if err := dec.Decode(&p); err != nil {
			return nil, err
		}
		pkgs = append(pkgs, p)
	}
	sort.Slice(pkgs, func(i, j int) bool { return pkgs[i].ImportPath < pkgs[j].ImportPath })
	return pkgs, nil
}

func goVersionAtLeast(v string, major, minor int) bool {
	var a, b int
	if _, err := fmt.Sscanf(v, "%d.%d", &a, &b); err != nil {
		return false
	}
	return a > major || (a == major && b >= minor)
}

func run(root string, testArgs []string) (*result, error) {
	pkgs, err := listPackages(root)
	if err != nil {
		return nil, err
	}
	if len(pkgs) == 0 {
		return nil, fmt.Errorf("no packages under %s", root)
	}
	tmp, err := os.MkdirTemp("", "gobranch-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tmp)
	hitsDir := filepath.Join(tmp, "hits")
	os.MkdirAll(hitsDir, 0o755)
	replace := map[string]string{}
	res := &result{Tool: map[string]string{"name": toolName, "version": toolVersion}}
	if v, err := goCmd(root, nil, "env", "GOVERSION").Output(); err == nil {
		res.GoVersion = strings.TrimSpace(string(v))
	}
	type pinfo struct {
		pkg   listPkg
		files []fileResult
	}
	var infos []pinfo
	for _, p := range pkgs {
		if p.Module == nil || !goVersionAtLeast(strings.TrimPrefix(p.Module.GoVersion, "go"), 1, 18) {
			return nil, fmt.Errorf("%s: the module needs a go directive of at least 1.18 (the instrumentation uses generics)", p.ImportPath)
		}
		if len(p.CgoFiles) > 0 {
			return nil, fmt.Errorf("%s: cgo files are not supported", p.ImportPath)
		}
		if len(p.EmbedFiles) > 0 {
			return nil, fmt.Errorf("%s: go:embed is not supported", p.ImportPath)
		}
		for _, ig := range p.IgnoredGoFiles {
			if !strings.HasSuffix(ig, "_test.go") {
				rel, _ := filepath.Rel(root, filepath.Join(p.Dir, ig))
				res.IgnoredFiles = append(res.IgnoredFiles, filepath.ToSlash(rel))
			}
		}
		for _, tf := range append(append([]string{}, p.TestGoFiles...), p.XTestGoFiles...) {
			src, err := os.ReadFile(filepath.Join(p.Dir, tf))
			if err != nil {
				return nil, err
			}
			if strings.Contains(string(src), "func TestMain(") {
				return nil, fmt.Errorf("%s: %s declares TestMain; the counters could not be flushed (not supported)", p.ImportPath, tf)
			}
		}
		fset := token.NewFileSet()
		next := 0
		var edges []Edge
		var files []fileResult
		for _, gf := range p.GoFiles {
			abs := filepath.Join(p.Dir, gf)
			src, err := os.ReadFile(abs)
			if err != nil {
				return nil, err
			}
			if s := string(src); strings.Contains(s, "//go:embed") || strings.Contains(s, "//go:linkname") {
				return nil, fmt.Errorf("%s: go:embed and go:linkname directives are not supported", abs)
			}
			rel, _ := filepath.Rel(root, abs)
			rel = filepath.ToSlash(rel)
			before := len(edges)
			inst, err := Instrument(fset, rel, src, &next, &edges)
			if err != nil {
				return nil, err
			}
			path := filepath.Join(tmp, fmt.Sprintf("%s__%s", strings.ReplaceAll(p.ImportPath, "/", "_"), gf))
			if err := os.WriteFile(path, inst, 0o644); err != nil {
				return nil, err
			}
			replace[abs] = path
			sum := sha256.Sum256(src)
			files = append(files, fileResult{Path: rel, SHA256: hex.EncodeToString(sum[:]), Edges: edges[before:]})
		}
		rt := filepath.Join(tmp, strings.ReplaceAll(p.ImportPath, "/", "_")+"__gobranch_rt.go")
		os.WriteFile(rt, []byte(runtimeSource(p.Name, next)), 0o644)
		replace[filepath.Join(p.Dir, "gobranch_rt.go")] = rt
		fl := filepath.Join(tmp, strings.ReplaceAll(p.ImportPath, "/", "_")+"__gobranch_flush_test.go")
		os.WriteFile(fl, []byte(flushSource(p.Name, p.ImportPath)), 0o644)
		replace[filepath.Join(p.Dir, "gobranch_flush_test.go")] = fl
		infos = append(infos, pinfo{p, files})
	}
	ov, _ := json.Marshal(map[string]map[string]string{"Replace": replace})
	ovPath := filepath.Join(tmp, "overlay.json")
	os.WriteFile(ovPath, ov, 0o644)
	args := append([]string{"test", "-overlay", ovPath, "-count=1"}, testArgs...)
	args = append(args, "./...")
	c := goCmd(root, []string{"GOBRANCH_OUT_DIR=" + hitsDir}, args...)
	c.Stdout, c.Stderr = os.Stderr, os.Stderr
	if err := c.Run(); err != nil {
		return nil, fmt.Errorf("go test failed: %v (no measurement is produced from a failing run)", err)
	}
	for _, in := range infos {
		hits := make([]int, 0)
		b, err := os.ReadFile(filepath.Join(hitsDir, strings.ReplaceAll(in.pkg.ImportPath, "/", "_")+".json"))
		if err == nil {
			var h struct {
				ImportPath string `json:"import_path"`
				Hits       []int  `json:"hits"`
			}
			if err := json.Unmarshal(b, &h); err != nil {
				return nil, err
			}
			hits = h.Hits
		} else if len(in.pkg.TestGoFiles)+len(in.pkg.XTestGoFiles) > 0 {
			return nil, fmt.Errorf("%s: its tests ran but wrote no counters", in.pkg.ImportPath)
		}
		pr := pkgResult{ImportPath: in.pkg.ImportPath}
		for _, f := range in.files {
			f.Hits = make([]int, len(f.Edges))
			for i, e := range f.Edges {
				if e.ID < len(hits) {
					f.Hits[i] = hits[e.ID]
				}
			}
			pr.Files = append(pr.Files, f)
		}
		res.Packages = append(res.Packages, pr)
	}
	sort.Strings(res.IgnoredFiles)
	return res, nil
}

func runtimeSource(pkg string, n int) string {
	if n == 0 {
		n = 1
	}
	return fmt.Sprintf(`package %s

import "sync/atomic"

var gobranchHits [%d]uint64

func gobranchHit(id int) { atomic.AddUint64(&gobranchHits[id], 1) }

func gobranchCond[T ~bool](id int, v T) T {
	if v {
		gobranchHit(id)
	} else {
		gobranchHit(id + 1)
	}
	return v
}
`, pkg, n)
}

func flushSource(pkg, importPath string) string {
	return fmt.Sprintf(`package %s

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func TestMain(m *testing.M) {
	code := m.Run()
	if dir := os.Getenv("GOBRANCH_OUT_DIR"); dir != "" {
		hits := make([]int, len(gobranchHits))
		for i := range gobranchHits {
			hits[i] = int(atomic.LoadUint64(&gobranchHits[i]))
		}
		b, _ := json.Marshal(map[string]interface{}{"import_path": %q, "hits": hits})
		if err := os.WriteFile(filepath.Join(dir, strings.ReplaceAll(%q, "/", "_")+".json"), b, 0o644); err != nil {
			code = 3
		}
	}
	os.Exit(code)
}
`, pkg, importPath, importPath)
}
