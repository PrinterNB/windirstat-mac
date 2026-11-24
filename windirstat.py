#!/usr/bin/env python3
"""Windirstat — WinDirStat's core for macOS: scan a folder, then browse it by size.

Run from a terminal:
    python3 windirstat.py                         pick a folder in the window, scan it, browse it
    python3 windirstat.py ~/Downloads             start from that folder
    python3 windirstat.py --print ~/Downloads     text mode, no window (for quick checks)
    python3 windirstat.py --print --depth 2 ~/Downloads
    python3 windirstat.py --print --alloc --top 10 ~/Downloads   du-style sizes + biggest files
    python3 windirstat.py --trash /tmp/mytrash    where marked items go (default ~/.Trash)

Flags: --print  text only | --alloc  allocated size as headline | --top N  show N biggest files
       --depth N  print depth (0 = everything) | --trash DIR  Trash root override

Settings live in config.json next to this file. Double-clickable from Finder after `sh build.sh`.
"""

import heapq
import json
import os
import shutil
import stat as stat_mod
import sys
import threading
import time
import tkinter
from tkinter import filedialog, messagebox

# ---------------------------------------------------------------- data model

# One flat list of fixed-slot lists. A node's index *is* its id, and a parent is
# always created before its children, so index order is topological: the
# aggregate pass below can walk it in reverse.
(
    NAME, PARENT, CHILD, SIB, KIND, FSIZE, FALLOC, SUBLOG, SUBALLOC,
    NFILES, EXPAND, MARK, DEV, DEPTH,
) = range(14)

K_DIR, K_FILE, K_LINK, K_VOL = "d", "f", "l", "v"

# Directory bundles: their bytes still count and the scanner still walks them,
# but the UI refuses to drill into them (they are apps and packages, not folders
# a person browses).
# Only unambiguously package-like folder suffixes: a plain folder someone happens
# to name "things.library" should stay browsable, so .library/.plugin are left out.
BUNDLE_SUFFIXES = (".app", ".photoslibrary", ".xcodeproj", ".pkg",
                   ".component", ".bundle")

DEFAULT_EXCLUDES = ["/System", "/private", "/Volumes", "/dev", "/proc"]
MAX_ENTRIES = 2_000_000
HOME = os.path.expanduser("~")


def short(path):
    return path.replace(HOME, "~", 1) if path.startswith(HOME) else path


def human(n):
    if abs(n) < 1000:
        return f"{n} B"
    v = n / 1024.0
    for u in ("KB", "MB", "GB", "TB"):
        if abs(v) < 1000:
            return f"{v:.1f} {u}"
        v /= 1024.0
    return f"{v:.1f} PB"


def is_excluded(globs, name, path):
    """A pattern is either a path (or path prefix, if it ends in /) or a name."""
    for g in globs:
        if g.endswith("/") and (path == g[:-1] or path.startswith(g)):
            return True
        if path == g or name == g:
            return True
        if name.lower() == g.lower():
            return True
    return False


# --------------------------------------------------------------------- scanner


def link(nodes, tail, par, child):
    first = tail.get(par)
    if first is None:
        nodes[par][CHILD] = child
    else:
        nodes[first][SIB] = child
    tail[par] = child


def scan(root, excludes, max_entries=MAX_ENTRIES, progress=None):
    """Full recursive walk. Returns (nodes, stats). Touches no Tk object."""
    root = os.path.abspath(root)
    rst = os.stat(root)
    nodes = [[root, -1, -1, -1, K_DIR, 0, 0, 0, 0, 0, True, 0, rst.st_dev, 0]]
    stats = {"root": root, "entries": 0, "files": 0, "dirs": 1, "links": 0,
             "volumes": 0, "special": 0, "inaccessible": 0, "excluded": 0,
             "bytes": 0, "stopped": False, "seconds": 0.0, "where": root,
             "vol_total": 0, "vol_free": 0}
    try:
        vol = os.statvfs(root)
        unit = vol.f_frsize or vol.f_bsize
        stats["vol_total"] = vol.f_blocks * unit
        stats["vol_free"] = vol.f_bavail * unit
    except OSError:
        pass
    hard = set()
    tail = {}
    stack = [(0, root)]
    t0 = time.time()
    entries = 0
    beat = t0

    while stack:
        cur, path = stack.pop()
        cn = nodes[cur]
        try:
            walker = os.scandir(path)
        except OSError:
            stats["inaccessible"] += 1
            continue
        try:
            for e in walker:
                name = e.name
                if path == "/" and (name.startswith(".") or
                                    name.lower().startswith("timemachine")):
                    # APFS snapshot roots (/.timemachine_*) exist only while a
                    # Time Machine backup is mounting them; skip them by name too.
                    stats["excluded"] += 1
                    continue
                if is_excluded(excludes, name, e.path):
                    stats["excluded"] += 1
                    continue
                try:
                    s = e.stat(follow_symlinks=False)
                except OSError:
                    stats["inaccessible"] += 1
                    continue
                mode = stat_mod.S_IFMT(s.st_mode)
                child = len(nodes)

                if stat_mod.S_ISDIR(mode):
                    if s.st_dev != cn[DEV]:
                        # mount point / another volume: report it, do not wander in
                        nodes.append([name, cur, -1, -1, K_VOL, 0, 0, 0, 0, 0,
                                      False, 0, s.st_dev, cn[DEPTH] + 1])
                        stats["volumes"] += 1
                    else:
                        nodes.append([name, cur, -1, -1, K_DIR, 0, 0, 0, 0, 0,
                                      not name.lower().endswith(BUNDLE_SUFFIXES),
                                      0, s.st_dev, cn[DEPTH] + 1])
                        stats["dirs"] += 1
                        stack.append((child, e.path))
                elif stat_mod.S_ISREG(mode):
                    if s.st_nlink > 1:
                        key = (s.st_dev, s.st_ino)
                        if key in hard:
                            continue  # this file was already counted by its other name
                        hard.add(key)
                    alloc = s.st_blocks * 512
                    nodes.append([name, cur, -1, -1, K_FILE, s.st_size, alloc,
                                  s.st_size, alloc, 1, False, 0, s.st_dev,
                                  cn[DEPTH] + 1])
                    stats["files"] += 1
                    stats["bytes"] += s.st_size
                elif stat_mod.S_ISLNK(mode):
                    # never followed, so a link back to a parent cannot loop the walk
                    nodes.append([name, cur, -1, -1, K_LINK, 0, 0, 0, 0, 0,
                                  False, 0, cn[DEV], cn[DEPTH] + 1])
                    stats["links"] += 1
                else:
                    stats["special"] += 1  # fifo, socket, door node, ...
                    continue

                link(nodes, tail, cur, child)
                entries += 1
                if progress is not None and (entries & 0x3FFF) == 0:
                    now = time.time()
                    if now - beat > 0.1:
                        beat = now
                        progress.update(entries=entries, files=stats["files"],
                                        dirs=stats["dirs"], bytes=stats["bytes"],
                                        where=path, seconds=now - t0)
                if entries >= max_entries:
                    stats["stopped"] = True
                    break
        except OSError:
            stats["inaccessible"] += 1
        finally:
            walker.close()
        if stats["stopped"]:
            break

    stats["seconds"] = time.time() - t0
    stats["entries"] = entries
    aggregate(nodes)
    return nodes, stats


def aggregate(nodes):
    """Fold child totals into parents in one reverse pass: O(N)."""
    for i in range(len(nodes) - 1, 0, -1):
        n = nodes[i]
        p = n[PARENT]
        if p < 0:
            continue
        par = nodes[p]
        par[SUBLOG] += n[SUBLOG]
        par[SUBALLOC] += n[SUBALLOC]
        par[NFILES] += n[NFILES]


def children(nodes, i):
    out, k = [], nodes[i][CHILD]
    while k >= 0:
        out.append(k)
        k = nodes[k][SIB]
    return out


def node_path(nodes, i):
    parts = []
    while i >= 0:
        parts.append(nodes[i][NAME])
        i = nodes[i][PARENT]
    parts.reverse()
    return os.path.join(*parts) if len(parts) > 1 else parts[0]


def to_trash(src, trash):
    """Rename on the same volume when possible, copy otherwise. Returns (dst, copied)."""
    base = os.path.basename(src.rstrip("/")) or "item"
    dst = os.path.join(trash, base)
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(trash, f"{base} {n}")
        n += 1
    try:
        os.rename(src, dst)
        return dst, False
    except OSError:
        shutil.move(src, dst)
        return dst, True


# --------------------------------------------------------------------- text mode


def head_of(cfg):
    return SUBLOG if cfg.get("size", "logical") == "logical" else SUBALLOC


def print_tree(nodes, stats, cfg, depth, top):
    head = head_of(cfg)
    basis = "on disk" if head == SUBALLOC else "logical"
    print(f"# {short(stats['root'])}  {human(nodes[0][SUBLOG])} logical   "
          f"{human(nodes[0][SUBALLOC])} on disk   {nodes[0][NFILES]:,} files   "
          f"{stats['dirs']:,} folders scanned")
    print(f"# sizes: 'logical' is st_size (what Finder shows); "
          f"'on disk' is st_blocks*512 (what du shows); sorted by {basis}")
    if stats.get("vol_total"):
        vt, vf = stats["vol_total"], stats["vol_free"]
        print(f"# volume: {human(vt)} total, {human(vt - vf)} used, {human(vf)} free")
    note = []
    if stats["links"]:
        note.append(f"{stats['links']} links")
    if stats["volumes"]:
        note.append(f"{stats['volumes']} volumes not entered")
    if stats["inaccessible"]:
        note.append(f"{stats['inaccessible']} inaccessible")
    if stats["excluded"]:
        note.append(f"{stats['excluded']} excluded")
    if stats["special"]:
        note.append(f"{stats['special']} special (fifo/socket/...)")
    if stats["stopped"]:
        note.append(f"STOPPED after {stats['entries']:,} entries")
    if note:
        print("# " + "   ".join(note))
    print(f"# scanned in {stats['seconds']:.1f}s")

    def walk(i, d):
        n = nodes[i]
        if i:
            par = nodes[n[PARENT]]
            pct = n[head] * 100.0 / par[head] if par[head] else 0.0
            tag = {"d": "", "f": "", "l": " @link", "v": " ^volume"}[n[KIND]]
            if n[KIND] == K_DIR and not n[EXPAND]:
                tag += " []bundle"
            if n[MARK] == 1:
                tag += " [mark]"
            elif n[MARK] == 2:
                tag += " [trashed]"
            print("  " * d + f"{short(n[NAME]) if d == 1 else n[NAME]}"
                              f"{tag}  {human(n[SUBLOG])} logical  "
                              f"{human(n[SUBALLOC])} on disk  {pct:.1f}%  {n[NFILES]:,}")
        if n[EXPAND] and (depth <= 0 or d < depth):
            for k in sorted(children(nodes, i),
                            key=lambda k: (-nodes[k][head], nodes[k][NAME])):
                walk(k, d + 1)

    walk(0, 0)

    if top:
        files = [(nodes[i][head], i) for i in range(1, len(nodes))
                 if nodes[i][KIND] == K_FILE and nodes[i][MARK] != 2]
        print("\n# biggest files")
        for _, i in heapq.nlargest(top, files):
            print(f"  {human(nodes[i][SUBLOG])} logical  "
                  f"{human(nodes[i][FALLOC])} on disk  {short(node_path(nodes, i))}")


# -------------------------------------------------------------------------- UI

W, H, TOP, ROW_H = 1040, 560, 40, 22
X_NAME, X_SIZE, X_ONDISK, X_PCT, X_CNT = 10, 640, 790, 900, 1030
FONT = ("Menlo", 12)
FONT_SMALL = ("Menlo", 11)
BG, FG = "#1b1f24", "#e9eef4"
ROW_ALT, SEL = "#20252b", "#33405c"
DIR_FILL, FILE_FILL = "#f2f6fb", "#bfc7d0"
DIM, FAINT, ACCENT = "#99a2ad", "#6f7a86", "#7fc2e8"
SORTS = [("biggest first", "size"), ("smallest first", "-size"),
         ("by name", "name"), ("most files first", "count")]
BUTTONS = [("Up", "up"), ("Sort", "sort"), ("Mark", "mark"),
           ("Delete marked", "delete"), ("Top N", "top"), ("Search", "search"),
           ("Rescan", "rescan"), ("Save settings", "save")]


class App:
    def __init__(self, root_path, cfg):
        self.cfg = cfg
        self.nodes, self.stats = [], {}
        self.cur = 0
        self.rows, self.sel, self.off = [], 0, 0
        self.sort = 0
        self.list_mode = None          # None | "top" | "search"
        self.query = ""
        self.scan_running = False
        self.progress = {}
        self.err = None
        self.note = None
        self.result = None
        self.root_path = root_path
        self.trash = cfg.get("trash") or os.path.expanduser("~/.Trash")

        t = self.tk = tkinter.Tk(className="Windirstat")
        t.geometry("1100x680")
        t.configure(bg=BG)
        self.canvas = tkinter.Canvas(t, width=W, height=H, bg=BG,
                                     highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        bar = tkinter.Frame(t, bg=BG)
        bar.pack(fill="x", pady=(2, 6))
        # tk.Button on macOS ignores/destroys the label once you set bg/fg, which is
        # what left the toolbar blank. Labels honour both, so the toolbar is a row of
        # clickable words wired to the same actions.
        for label, act in BUTTONS:
            item = tkinter.Label(bar, text=label, bg=BG, fg=ACCENT,
                                 font=FONT_SMALL, cursor="hand2")
            item.pack(side="left", padx=8)
            item.bind("<Button-1>", lambda e, a=act: self.act(a))
            item.bind("<Double-Button-1>", lambda e, a=act: self.act(a))
        c = self.canvas
        c.bind("<MouseWheel>", lambda e: self.wheel(1))
        c.bind("<Shift-MouseWheel>", lambda e: self.wheel(-1))
        c.bind("<Button-1>", self.click)
        for key, act in [("Up", "wheel-"), ("Down", "wheel+"), ("Left", "up"),
                         ("Right", "drill"), ("Return", "drill"), ("s", "sort"),
                         ("m", "mark"), ("d", "delete"), ("t", "top"),
                         ("slash", "search"), ("r", "rescan")]:
            c.bind("<Key-" + key + ">", lambda e, a=act: self.act_key(a))
            t.bind("<Key-" + key + ">", lambda e, a=act: self.act_key(a))
        t.bind("<Key-q>", lambda e: self.quit())
        t.focus_force()

    def visible(self):
        return max(1, (H - TOP) // ROW_H)

    def act(self, a):
        {"up": self.up, "sort": self.sort_cycle, "mark": self.mark,
         "delete": self.delete, "top": self.show_top, "search": self.search,
         "rescan": self.rescan, "save": self.save}[a]()

    def act_key(self, a):
        if a == "wheel-":
            self.wheel(-1)
        elif a == "wheel+":
            self.wheel(1)
        else:
            self.act(a)

    def wheel(self, d):
        self.off = max(0, min(max(0, len(self.rows) - self.visible()),
                              self.off + d * 3))
        self.redraw()

    def click(self, e):
        i = (e.y - TOP) // ROW_H + self.off
        self.sel = i if 0 <= i < len(self.rows) else -1
        self.redraw()

    # ---- view ------------------------------------------------------------

    def refresh(self):
        if self.list_mode == "top":
            self.rows = self.top_files()
        elif self.list_mode == "search":
            self.rows = self.search_rows()
        else:
            self.rows = children(self.nodes, self.cur) if self.nodes else []
        self.rows.sort(key=self.sort_key())
        self.sel = 0 if self.rows else -1
        self.off = 0
        self.redraw()

    def sort_key(self):
        n, head = self.nodes, head_of(self.cfg)

        def size(i):
            return n[i][head]

        label, mode = SORTS[self.sort]
        if mode == "name":
            return lambda i: (n[i][NAME].lower(), -size(i))
        if mode == "count":
            return lambda i: (-n[i][NFILES], -size(i), n[i][NAME])
        if mode == "-size":
            return lambda i: (size(i), n[i][NAME])
        return lambda i: (-size(i), n[i][NAME])

    def sort_cycle(self):
        self.sort = (self.sort + 1) % len(SORTS)
        self.refresh()

    def up(self):
        if self.cur:
            self.cur = self.nodes[self.cur][PARENT]
            self.list_mode = None
            self.refresh()
        else:
            self.flash("already at the folder you scanned")

    def drill(self):
        if not self.rows:
            self.flash("nothing here")
            return
        i = self.rows[self.sel] if self.sel >= 0 else self.rows[0]
        n = self.nodes[i]
        if n[KIND] != K_DIR:
            self.flash("that row is a file, not a folder")
            return
        if not n[EXPAND]:
            self.flash("bundle: its bytes are counted, but it is not browsable")
            return
        self.cur = i
        self.list_mode = None
        self.refresh()

    def flash(self, msg):
        self.note = msg
        self.redraw()

    def draw_row(self, c, slot, ni, selected):
        n, head = self.nodes, head_of(self.cfg)
        n = n[ni]
        y = TOP + slot * ROW_H
        if slot % 2:
            c.create_rectangle(2, y, W - 4, y + ROW_H - 2, fill=ROW_ALT)
        if selected:
            c.create_rectangle(2, y, W - 4, y + ROW_H - 2, fill=SEL)
        name = n[NAME]
        if n[KIND] == K_LINK:
            name += "  @ link"
        elif n[KIND] == K_VOL:
            name += "  ^ other volume"
        elif n[KIND] == K_DIR and not n[EXPAND]:
            name += "  [] bundle"
        if n[MARK] == 1:
            name += "  [mark]"
        elif n[MARK] == 2:
            name += "  [trashed]"
        fg = FILE_FILL if n[KIND] in (K_FILE, K_LINK) else DIR_FILL
        if n[KIND] == K_VOL or n[MARK]:
            fg = ACCENT
        c.create_text(X_NAME, y + 2, anchor="nw", text=name, fill=fg, font=FONT)
        c.create_text(X_SIZE, y + ROW_H - 3, anchor="se", text=human(n[SUBLOG]),
                      fill=FG, font=FONT)
        c.create_text(X_ONDISK, y + ROW_H - 3, anchor="se",
                      text=human(n[SUBALLOC]), fill=DIM, font=FONT)
        par = self.nodes[n[PARENT]] if n[PARENT] >= 0 else n
        pct = n[head] * 100.0 / par[head] if par[head] else 0.0
        c.create_text(X_PCT, y + ROW_H - 3, anchor="se", text=f"{pct:.1f}%",
                      fill=DIM, font=FONT)
        c.create_text(X_CNT, y + ROW_H - 3, anchor="se", text=f"{n[NFILES]:,}",
                      fill=DIM, font=FONT)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.nodes:
            head = head_of(self.cfg)
            chain, i = [], self.cur
            while i >= 0:
                chain.append(i)
                i = self.nodes[i][PARENT]
            chain.reverse()
            crumbs = []
            for k in chain:
                n = self.nodes[k]
                nm = short(n[NAME]) if k == 0 else n[NAME]
                crumbs.append(nm if k == 0 else f"{nm}  {human(n[head])}")
            c.create_text(8, 10, anchor="nw", text="  ›  ".join(crumbs[-4:])[:W - 20],
                          fill=DIM, font=FONT)
            what = {"top": "biggest files in", "search": "matches for",
                    None: "contents of"}[self.list_mode]
            where = self.query if self.list_mode == "search" else \
                self.nodes[self.cur][NAME] if self.cur else short(self.root_path)
            c.create_text(X_SIZE, 10, anchor="nw",
                          text=f"{what} {where}"[:W - 20], fill="#5a6068", font=FONT)
            c.create_text(8, 24, anchor="nw", font=FONT_SMALL, fill=ACCENT,
                          text=("columns:  name   size (logical, what Finder shows)   "
                                "on disk (what du shows)   % of folder   files")
                               + f"   sorted by {'on disk' if head == SUBALLOC else 'logical'}")
            c.create_text(W - 8, 10, anchor="ne",
                          text=f"{len(self.rows)} rows, showing {self.off + 1}-"
                               f"{min(len(self.rows), self.off + self.visible())}",
                          fill=DIM, font=FONT)
        vis = self.visible()
        for slot in range(vis):
            i = self.off + slot
            if i >= len(self.rows):
                break
            self.draw_row(c, slot, self.rows[i], i == self.sel)
        lines = []
        if self.stats:
            s = self.stats
            lines.append(f"{s['files']:,} files   {s['dirs']:,} folders   "
                         f"{s['links']} links   {s['volumes']} volumes")
            if self.nodes:
                lines.append(f"space used by it:  {human(self.nodes[0][SUBLOG])} logical"
                             f"   {human(self.nodes[0][SUBALLOC])} on disk")
                vt, vf = s.get("vol_total", 0), s.get("vol_free", 0)
                if vt:
                    lines.append(f"on this volume:  {human(vt)} total   "
                                 f"{human(vt - vf)} used   {human(vf)} free")
            bad = []
            if s["inaccessible"]:
                bad.append(f"{s['inaccessible']} inaccessible")
            if s["excluded"]:
                bad.append(f"{s['excluded']} excluded")
            if s["special"]:
                bad.append(f"{s['special']} special")
            if s["stopped"]:
                bad.append(f"STOPPED after {s['entries']:,} entries")
            if bad:
                lines.append("  ".join(bad))
        if self.scan_running:
            p = self.progress
            lines.append(f"scanning: {p.get('files', 0):,} files  "
                         f"{human(p.get('bytes', 0))}  {p.get('seconds', 0.0):.0f}s  "
                         f"at {short(p.get('where', ''))}")
        if self.err:
            lines.append("!! " + self.err)
        if self.note:
            lines.append("» " + self.note)
            self.note = None
        if lines:
            c.create_text(8, TOP + vis * ROW_H + 8, anchor="nw",
                          text="\n".join(lines), fill=DIM, font=FONT_SMALL)

    # ---- actions ---------------------------------------------------------

    def top_files(self):
        n, head = self.nodes, head_of(self.cfg)
        pool = [(n[i][head], i) for i in range(1, len(n))
                if n[i][KIND] == K_FILE and n[i][MARK] != 2]
        k = max(1, int(self.cfg.get("top_n", 20)))
        return [i for _, i in heapq.nlargest(k, pool)]

    def show_top(self):
        self.list_mode = "top"
        self.refresh()

    def search_rows(self):
        n, head, q = self.nodes, head_of(self.cfg), self.query.lower()
        rows = [i for i in range(1, len(n)) if q in n[i][NAME].lower()]
        if len(rows) > 400:
            rows = [i for _, i in heapq.nlargest(400, ((n[i][head], i) for i in rows))]
        return rows

    def search(self):
        if getattr(self, "qtop", None):
            return
        top = tkinter.Toplevel(self.tk, width=460, height=64)
        top.configure(bg=BG)
        top.geometry("460x64")
        e = tkinter.Entry(top, takefocus=1, bg="#14171b", fg=FG,
                          insertbackground=FG)
        e.pack()
        tkinter.Label(top, text="name contains (or an extension like .png):",
                      bg=BG, fg=DIM, anchor="w").pack(side="left", padx=8)
        e.bind("<Return>", self.take_query)
        e.bind("<Escape>", lambda ev: top.destroy())
        self.qtop, self.qentry = top, e

    def take_query(self, event):
        q = self.qentry.get().strip()
        self.qtop.destroy()
        self.qtop = self.qentry = None
        if not q:
            self.flash("search needs some text")
            return
        self.query = q
        self.list_mode = "search"
        self.refresh()

    def mark(self):
        if not self.rows or self.sel < 0:
            self.flash("pick a row first")
            return
        i = self.rows[self.sel]
        n = self.nodes[i]
        if n[MARK] == 2:
            self.flash("already in the Trash")
            return
        want = 0 if n[MARK] else 1
        n[MARK] = want
        delta = -1 if want else 1
        p = n[PARENT]
        while p >= 0:
            par = self.nodes[p]
            par[SUBLOG] += delta * n[SUBLOG]
            par[SUBALLOC] += delta * n[SUBALLOC]
            par[NFILES] += delta * n[NFILES]
            p = self.nodes[p][PARENT]
        self.refresh()

    def delete(self):
        marked = [i for i in range(1, len(self.nodes)) if self.nodes[i][MARK] == 1]
        if not marked:
            self.flash("nothing is marked")
            return
        # only the outermost marked item moves: an ancestor covers its whole subtree
        topmost = [i for i in marked if self.nodes[i][PARENT] < 0
                   or self.nodes[self.nodes[i][PARENT]][MARK] != 1]
        head = head_of(self.cfg)
        total = sum(self.nodes[i][SUBLOG] for i in topmost)
        same_vol = True
        try:
            os.makedirs(self.trash, exist_ok=True)
            # the scan already recorded each item's device, so no extra stat
            same_vol = self.nodes[topmost[0]][DEV] == os.stat(self.trash).st_dev
        except OSError as exc:
            self.flash(f"cannot use Trash {self.trash}: {exc.strerror}")
            return
        if not same_vol and not messagebox.askyesno(
                "Different volume",
                f"{human(total)} sits on another volume than {self.trash}, so it has to be "
                f"copied before it can be removed. Copy it now?"):
            self.flash("cancelled - marks kept, nothing was moved")
            return
        moved, copied = 0, 0
        for i in topmost:
            src = node_path(self.nodes, i)
            if not os.path.exists(src):
                self.nodes[i][MARK] = 2
                continue
            try:
                _, was_copied = to_trash(src, self.trash)
                copied += was_copied
                self.nodes[i][MARK] = 2
                moved += 1
            except OSError as exc:
                self.flash(f"cannot move {self.nodes[i][NAME]}: {exc.strerror}")
        self.refresh()
        msg = f"{moved} item(s), {human(total)} → {self.trash}"
        if copied:
            msg += f"   ({copied} needed a copy because it is on another volume)"
        self.flash(msg)

    def rescan(self):
        self.scan_start(self.root_path)

    def save(self):
        path = os.path.join(SCRIPT_DIR, "config.json")
        try:
            with open(path, "w") as fh:
                json.dump(self.cfg, fh, indent=2, sort_keys=True)
            self.flash(f"settings written to {path}")
        except OSError as exc:
            self.flash(f"cannot write config.json: {exc.strerror}")

    # ---- scanning --------------------------------------------------------

    def scan_start(self, path):
        self.root_path = path
        self.scan_running = True
        self.result = None
        self.err = None
        self.progress = {"entries": 0, "files": 0, "dirs": 1, "bytes": 0,
                         "where": path, "seconds": 0.0}
        self.redraw()
        threading.Thread(target=self._do_scan, args=(path,),
                         name="scanner", daemon=True).start()
        self.tk.after(150, self.tick)

    def _do_scan(self, path):
        try:
            self.result = scan(path, self.cfg.get("excludes", DEFAULT_EXCLUDES),
                               MAX_ENTRIES, self.progress)
        except Exception as exc:
            self.err = f"{type(exc).__name__}: {exc}"
            self.result = None

    def tick(self):
        if self.err or not self.scan_running:
            self.redraw()
            return
        if self.result is not None:
            self.scan_running = False
            self.nodes, self.stats = self.result
            self.result = None
            self.cur = 0
            self.list_mode = None
            self.refresh()
            return
        self.redraw()
        self.tk.after(150, self.tick)

    def quit(self):
        self.tk.destroy()

    def loop(self):
        self.tk.mainloop()


SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


def load_cfg():
    cfg = {"excludes": list(DEFAULT_EXCLUDES), "size": "logical",
           "top_n": 20, "trash": ""}
    path = os.path.join(SCRIPT_DIR, "config.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for k in ("excludes", "size", "top_n", "trash"):
                if k in data:
                    cfg[k] = data[k]
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        print(f"# config.json ignored: {exc}")
    return cfg


def main(argv):
    args, mode, depth, top, trash, path = list(argv[1:]), "gui", 3, 0, None, None
    cfg = load_cfg()
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--print":
            mode = "print"
        elif a == "--alloc":
            cfg["size"] = "alloc"
        elif a == "--logical":
            cfg["size"] = "logical"
        elif a in ("--top", "--depth"):
            i += 1
            try:
                v = int(args[i])
            except (IndexError, ValueError):
                print("need a number after --top / --depth")
                return 2
            if a == "--top":
                top = v
            else:
                depth = v
        elif a == "--trash":
            i += 1
            if i >= len(args):
                print("--trash needs a folder")
                return 2
            cfg["trash"] = trash = os.path.abspath(args[i])
        elif a.startswith("-"):
            print(f"unknown flag {a} (flags: --print --alloc --top N --depth N --trash DIR)")
            return 2
        else:
            path = a
        i += 1

    if path is None and mode == "gui":
        d = filedialog.askdirectory(title="Folder to scan", mustexist=True)
        if not d:
            messagebox.showinfo(
                "No folder chosen",
                "You picked no folder, so there is nothing to scan. "
                "Run the app again and choose one - or pass its path as an argument.")
            return 0
        path = d
    if path is None:
        print("usage: windirstat.py --print <folder> [--alloc] [--top N] [--depth N]")
        return 2

    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        print(f"not a folder: {path}")
        return 2

    if mode == "print":
        nodes, stats = scan(path, cfg["excludes"], MAX_ENTRIES)
        print_tree(nodes, stats, cfg, depth, top)
        return 0

    app = App(path, cfg)
    app.scan_start(path)
    app.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
