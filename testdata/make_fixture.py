#!/usr/bin/env python3
"""Builds testdata/tiny/ - a fixture tree with numbers we can check against.

    python3 -I testdata/make_fixture.py        # rebuild it, print what it expects
    python3 -I windirstat.py --print --depth 0 testdata/tiny

The tree is deliberately awkward: a bundle, a hidden dir, a hardlinked pair, a
symlink back to the folder itself, a fifo, and a folder no one may read.
"""

import os
import stat

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                    "testdata", "tiny")

FILES = {
    "a.txt": 1000,
    "b.txt": 2000,
    "zero.bin": 40000,                      # all zeros: APFS may store it smaller
    "deep/nested/deeper/deepest.txt": 700,
    "docs/Foo.app/Contents/MacOS/Foo": 300,  # Foo.app is a bundle, not a folder
    ".git/hooks/index": 500,                 # hidden dirs inside a normal root do count
    "hard1": 900,                            # two names, one file: counted once
}
LINKED = ("hard1", "hard2")                   # os.link, so hard2 adds no bytes
SYMLINKS = {"link_a.txt": "a.txt", "loop": ROOT}   # loop points at the fixture root


def build():
    if os.path.isdir(ROOT):
        import shutil
        locked = os.path.join(ROOT, "noperm")
        if os.path.isdir(locked):
            os.chmod(locked, 0o755)  # a mode-000 dir cannot be walked, so unlock first
        shutil.rmtree(ROOT)
    os.makedirs(ROOT)
    for rel, size in FILES.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"\0" * size)
    first, second = (os.path.join(ROOT, n) for n in LINKED)
    os.link(first, second)

    locked = os.path.join(ROOT, "noperm")
    os.makedirs(locked, exist_ok=True)
    with open(os.path.join(locked, "unreadable.txt"), "wb") as fh:
        fh.write(b"x" * 5000)          # must never be seen by the scanner
    # macOS can keep the owner read bit on (ACLs), so try the modes that actually
    # deny listing and confirm it, instead of assuming chmod 0 worked.
    # Denying listing is not guaranteed inside this workspace (an extended
    # attribute set by Finder has been seen keeping the owner read bit), so verify
    # it here rather than assuming chmod worked. The denial path itself is also
    # checked against a real root-owned folder (/private/var/db/sudo).
    for mode in (0, 0o111, 0o311):
        os.chmod(locked, mode)
        try:
            os.scandir(locked)
        except PermissionError:
            print(f"  noperm mode set to {oct(mode)}: listing denied (good)")
            break
    else:
        print("  WARNING: could not make noperm unreadable on this machine")

    os.mkfifo(os.path.join(ROOT, "fifo"))
    for name, target in SYMLINKS.items():
        os.symlink(target, os.path.join(ROOT, name))


def expected():
    total = sum(FILES.values())          # hardlink pair counted once, unreadable dir unseen
    return {
        "root logical": total,
        "root files": len(FILES),        # hard2 is the same file, not a new one
        "folders": 11,  # tiny, deep, nested, deeper, docs, Foo.app, .git, hooks, noperm,
        # Foo.app/Contents, Foo.app/Contents/MacOS - a bundle's bytes are still walked
        "inaccessible": "1 when the dir really locked (else the ACL overrode chmod;"
                        " the denial path is checked separately on /private/var/db/sudo)",
        "links": len(SYMLINKS),
        "special": 1,                   # the fifo
    }


if __name__ == "__main__":
    build()
    exp = expected()
    print(f"built {ROOT}")
    for k, v in sorted(exp.items()):
        print(f"  {k}: {v}")
    print("  note: 'unreadable.txt' 5000 B must NOT appear in the totals")
    print("  note: Foo.app bytes (300) must be counted but flagged [] bundle")
