# Implicit `iota`: a reproduced unsafe reuse

Two commit states of one Go package; `B` repeats the expression of `A` (`A = iota + N`, then `B`).

| | `a/c.go` | `b/c.go` |
|---|---|---|
| `A` | `iota + 1` | `iota + 2` |
| `B` | `B` (value 2) | `B` (value 3) |
| `TestB` (wants `B == 2`) | passes | **fails** |

## What the lab's graph does

`lab/extractor/typeddeps` makes one node per constant name. A spec without values (`B`) has the source text `B`, and the walk over
its values is empty (`walk` in `main.go`, the `ValueSpec` case), so `B` has **no edge** to `A` and no hint of its position in the
group. Observed (`observed-*.json`): the source digest of `B` is the same in `a` and `b`; the Merkle key of `UseB` and of `TestB` is
the same in `a` and `b`; `B.deps` is empty. So every rule of `RESULTS.md` sections 4 and 5 would reuse the passing result of `a`
for `b`, where the test fails: a **false reuse**, the first one reproduced.

This is kept as a known RED case on purpose; the extractor is not changed in this commit.

## Reproduce

```
python3 lab/measure/repro_iota.py PATH/TO/typeddeps --expect vulnerable [--record OUT.json]
```

Exit 0 when all four facts hold. After the extractor is fixed the same script is run with `--expect fixed`.

## Observed with

Go 1.26.9 and `golang.org/x/tools` v0.44.0 (the lab's `go.mod` pins Go 1.27.2 and x/tools v0.38.0; neither was available on the
machine that recorded this, and the extractor was built from a copy with those two lines changed). The result depends on the
syntax tree only, but **it is not yet confirmed with the pinned Go 1.27.2 in CI**, and until it is, the case is established on the
tested configuration only.

## Not enough for the fix: position

An edge `B -> A` is not enough. If a constant is inserted between `A` and `B`, the text of `B` stays `B` while its `iota`
changes. The node of an implicit spec must carry the inherited expression and its index in the group, so that either changing
changes the key.
