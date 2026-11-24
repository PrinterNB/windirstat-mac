#!/usr/bin/env python3
"""Builds the real window over testdata/tiny, drives it, then tears it down.

    python3 -I testdata/smoke_gui.py

This is a smoke test, not a look test: it proves the window builds, the scan
finishes, sorting/drilling/marking renumber correctly, and closing works. It
cannot tell you whether the rows look right - open the app for that.
"""

import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
FOLDER = os.path.join(PROJ, "testdata", "tiny")
sys.path.insert(0, PROJ)  # -I keeps only the script's own folder on the path
import windirstat as W  # noqa: E402

a = W.App(FOLDER, W.load_cfg())
a.scan_start(FOLDER)


def step():
    if a.scan_running:
        a.tk.after(200, step)
        return
    s = a.stats
    print(f"scanned: {s['files']} files  {s['dirs']} folders  {s['links']} links  "
          f"{s['volumes']} volumes  {s['inaccessible']} inaccessible  rows {len(a.rows)}")
    for m in range(len(W.SORTS)):
        a.sort = m
        a.refresh()
        print(f"  {W.SORTS[m][0]}: " + ", ".join(
            f"{a.nodes[i][0]}={a.nodes[i][W.SUBLOG]}" for i in a.rows[:3]))
    dirs = [i for i in a.rows if a.nodes[i][W.KIND] == "d" and a.nodes[i][W.EXPAND]]
    a.cur = dirs[0]
    a.refresh()
    print(f"drilled into {a.nodes[a.cur][0]}: {len(a.rows)} rows")
    a.mark()
    marked = [i for i in range(1, len(a.nodes)) if a.nodes[i][W.MARK] == 1]
    print(f"marked {[a.nodes[i][0] for i in marked]} -> root total is now "
          f"{a.nodes[0][W.SUBLOG]} (was {a.stats['bytes']})")
    a.list_mode = "top"
    a.refresh()
    print("biggest files: " + ", ".join(
        f"{a.nodes[i][0]}={a.nodes[i][W.SUBLOG]}" for i in a.rows[:3]))
    a.trash = "/tmp/wsmoke/trash"          # not ~/.Trash, so the test stays out of it
    os.makedirs(a.trash, exist_ok=True)
    a.delete()                             # same volume, so no confirm dialog fires
    gone = [i for i in range(1, len(a.nodes)) if a.nodes[i][W.MARK] == 2]
    print("now in Trash:", [a.nodes[i][0] for i in gone])
    for i in gone:
        src = W.node_path(a.nodes, i)
        print(f"  {src} still there? {os.path.exists(src)}")
    print("root total after deleting:", a.nodes[0][W.SUBLOG])
    print("trash holds:", sorted(os.listdir(a.trash)))
    a.list_mode = "search"
    a.query = ".txt"          # set directly: the typed dialog needs a real click
    a.refresh()
    print("search '.txt' rows:", [a.nodes[i][0] for i in a.rows])
    a.tk.after(150, a.tk.destroy)


a.tk.after(400, step)
a.tk.mainloop()
print("window torn down cleanly")
