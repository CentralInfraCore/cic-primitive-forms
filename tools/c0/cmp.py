"""Compare two derived-id tables: cmp.py base.tsv new.tsv
Columns: id, file, function, from, to, ordinal, body digest (16), old whole-file id."""
import sys, collections


def load(p):
    return [l.rstrip("\n").split("\t") for l in open(p)]


base, new = load(sys.argv[1]), load(sys.argv[2])
bm = {r[0]: r for r in base}
nm = {r[0]: r for r in new}
same_reuse = [i for i in bm if i in nm and bm[i][6] == nm[i][6]]            # same id, same function body: a reuse candidate
same_changed_body = [i for i in bm if i in nm and bm[i][6] != nm[i][6]]     # same id, the function changed
gone = [i for i in bm if i not in nm]
added = [i for i in nm if i not in bm]
old_same = len({r[7] for r in base} & {r[7] for r in new})                  # the whole-file-digest ids that survive today
print("mutants base %d, new %d" % (len(base), len(new)))
print("  stable id kept, function body unchanged (reuse candidates): %d" % len(same_reuse))
print("  stable id kept, function body changed:                      %d" % len(same_changed_body))
print("  id gone:                                                    %d" % len(gone))
print("  id new:                                                     %d" % len(added))
print("  today's whole-file-digest ids that survive:                 %d" % old_same)
by = collections.Counter()
for i in gone:
    by[(bm[i][1], bm[i][2])] += 1
for i in added:
    by[(nm[i][1], nm[i][2])] += 1
for i in same_changed_body:
    by[(nm[i][1], nm[i][2])] += 1
funcs = sorted({(r[1], r[2]) for r in base + new if (r[1], r[2]) in by})
print("  functions touched: %s" % ", ".join("%s:%s" % f for f in funcs[:8]) + (" ..." if len(funcs) > 8 else ""))
