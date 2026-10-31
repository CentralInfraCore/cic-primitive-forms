"""Merkle keys over a dependency graph (the schema of typeddeps / scipdeps).

    python3 lab/measure/merkle.py graph.json > keys.json

The key of a node is the SHA-256 of its own source digest and the keys of everything it depends on. A cycle (mutual recursion,
a type and its methods) is one strongly connected component: its members share a key made of all their source digests and the
keys of the components the cycle depends on. An edge to an item outside the graph (the standard library, a crate or module
dependency) enters the key by its id only; the version of what it names is the business of the environment key (lockfiles,
toolchain), not of this one.

So two commits give a node the same key exactly when the source of every declaration in its closure is byte for byte the same
and the closure has the same shape.
"""
import hashlib
import json
import sys


def sccs(nodes, deps):
    """Tarjan's algorithm, iterative; returns the components in reverse topological order (dependencies first)."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(deps.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in nodes:
                    continue
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(deps.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
    return out


def keys(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    deps = {n["id"]: set(n["deps"]) for n in graph["nodes"]}
    comp_of, key = {}, {}
    for comp in sccs(set(nodes), deps):
        h = hashlib.sha256()
        members = set(comp)
        for m in comp:
            h.update(("node %s %s\n" % (m, nodes[m]["src_sha256"])).encode())
        outside = set()
        for m in comp:
            for d in deps[m]:
                if d in members:
                    h.update(("cycle %s %s\n" % (m, d)).encode())
                elif d in nodes:
                    outside.add("dep %s %s\n" % (d, key[d]))
                else:
                    outside.add("external %s\n" % d)
        for line in sorted(outside):
            h.update(line.encode())
        k = h.hexdigest()
        for m in comp:
            key[m] = k
            comp_of[m] = len(comp)
    return key, comp_of


def main():
    graph = json.load(open(sys.argv[1]))
    key, comp_of = keys(graph)
    json.dump({"keys": key, "largest_cycle": max(comp_of.values()) if comp_of else 0}, sys.stdout, indent=0, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
