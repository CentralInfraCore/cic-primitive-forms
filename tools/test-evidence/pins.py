"""The actions the workflows use are pinned to a commit (docs/EVIDENCE-PRODUCTION.md, "Pinned actions").

A tag such as `v4` is a label that its owner can move; the validity key of an evidence record carries the revision of every
action, so a workflow has to name it. `scan` reads every `uses:` under `.github/` and returns the revision of each action and
what is not pinned. A reference is fine if it is local (`./...`) or `owner/repo[/path]@<40 lower case hex>`; a tag, a branch, a
short or an upper case id, a `docker://` image, a missing `@` are not. One action at two revisions is a conflict: the
environment of a record names one revision per action.
"""
import os
import re

FULL = re.compile(r"[0-9a-f]{40}\Z")
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")


def scan(root):
    """(actions, problems): the revision of each external action, and one line per reference that is not acceptable."""
    actions, problems, seen = {}, [], {}
    base = os.path.join(root, ".github")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            if not name.endswith((".yml", ".yaml")):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                for no, line in enumerate(fh, 1):
                    m = USES.match(line)
                    if not m:
                        continue
                    ref = m.group(1).strip("'\"")
                    where = "%s:%d" % (rel, no)
                    if ref.startswith("./"):
                        continue
                    if ref.startswith("docker://"):
                        problems.append("%s: %s is an image, not a pinned action" % (where, ref))
                        continue
                    name_, at, rev = ref.partition("@")
                    if not at or not name_ or "/" not in name_:
                        problems.append("%s: %s has no revision" % (where, ref))
                    elif not FULL.match(rev):
                        problems.append("%s: %s is not a full lower case commit id" % (where, ref))
                    else:
                        if seen.setdefault(name_, rev) != rev:
                            problems.append("%s: %s at %s, elsewhere at %s" % (where, name_, rev, seen[name_]))
                        actions[name_] = seen[name_]
    return dict(sorted(actions.items())), problems
