"""If function f is edited, how many mutants can have a different verdict? impact.py closure.tsv ids-base.tsv
A mutant of g depends on every function in the call closure of g (name based over approximation: a selector call x.m() reaches every
method named m), so editing f can change the verdict of every mutant of every g with f in closure(g)."""
import sys, collections, statistics

closure = {}
for l in open(sys.argv[1]):
    q, rest = l.rstrip("\n").split("\t")
    closure[q] = set(rest.split(","))
mutants_of = collections.Counter()
for l in open(sys.argv[2]):
    r = l.split("\t")
    mutants_of[r[2]] += 1
total = sum(mutants_of.values())
decl_level = sum(v for k, v in mutants_of.items() if k.startswith("<"))
print("mutants %d; in functions %d; in package level declarations %d" % (total, total - decl_level, decl_level))
funcs = sorted(closure)
rows = []
for f in funcs:
    own = mutants_of.get(f, 0)
    reach = sum(mutants_of.get(g, 0) for g in funcs if f in closure[g])
    rows.append((reach, own, f))
rows.sort(reverse=True)
reaches = [r[0] for r in rows]
print("functions: %d, with mutants: %d" % (len(funcs), sum(1 for f in funcs if mutants_of.get(f))))
print("edit of one function, mutants whose verdict may change (own body only = digest rule / callee closure rule):")
own_only = [r[1] for r in rows]
print("  digest rule (own body):  median %d, mean %.1f, max %d" % (statistics.median(own_only), statistics.mean(own_only), max(own_only)))
print("  closure rule:            median %d, mean %.1f, max %d  (of %d)" % (statistics.median(reaches), statistics.mean(reaches), max(reaches), total))
print("  closure rule, share of all mutants: median %.1f%%, mean %.1f%%" % (100 * statistics.median(reaches) / total, 100 * statistics.mean(reaches) / total))
print("top 8 by reach:")
for reach, own, f in rows[:8]:
    print("  %-28s reach %4d  own %3d" % (f, reach, own))
share = collections.Counter()
for reach, own, f in rows:
    b = reach / total
    share["<=5%" if b <= 0.05 else "<=25%" if b <= 0.25 else "<=50%" if b <= 0.5 else ">50%"] += 1
print("functions by share of all mutants a change reaches:", dict(share))
