"""A Rust crate's dependency graph the way the lab makes it: rust-analyzer's SCIP index, read by scipdeps.

    from rust_graph import graph, passes

`toolchain` names the rustup toolchain that has rust-analyzer (RUST_TOOLCHAIN in CI). `env` adds variables, e.g.
SCIPDEPS_PLANTED_NO_IMPL=1 for the extractor without the type -> impl edges.
"""
import json
import os
import subprocess
import tempfile


def graph(crate, scipdeps, toolchain, env=None):
    """The graph (schema cic-lab/typeddeps/v2, a dict) of the crate in `crate`."""
    with tempfile.TemporaryDirectory() as d:
        scip = os.path.join(d, "index.scip")
        subprocess.run(["rustup", "run", toolchain, "rust-analyzer", "scip", ".", "--output", scip], cwd=crate, check=True,
                       capture_output=True)
        out = subprocess.run([os.path.abspath(scipdeps), scip, os.path.abspath(crate)], check=True, capture_output=True, text=True,
                             env=dict(os.environ, **(env or {})))
    return json.loads(out.stdout)


def passes(crate, toolchain):
    with tempfile.TemporaryDirectory() as target:
        return subprocess.run(["rustup", "run", toolchain, "cargo", "test", "--offline", "-q"], cwd=crate, capture_output=True,
                              env=dict(os.environ, CARGO_TARGET_DIR=target)).returncode == 0


def toolchain_identity(toolchain):
    """What of the toolchain the semantics depend on: the compiler and rust-analyzer versions (the environment key of the evidence)."""
    def run(*cmd):
        return subprocess.run(["rustup", "run", toolchain, *cmd], capture_output=True, text=True).stdout.strip()

    ra = subprocess.run(["rustup", "which", "--toolchain", toolchain, "rust-analyzer"], capture_output=True, text=True).stdout.strip()
    return {"rustc": run("rustc", "-V"), "rust-analyzer": run("rust-analyzer", "--version"), "rust-analyzer-path": ra}
