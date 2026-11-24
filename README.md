# Windirstat

Directory size browser for **macOS** — the part of WinDirStat that answers "what is
taking up my space", without the colored treemap.

> Ported to macOS from the shape of WinDirStat: pick a folder, scan all of it once,
> then browse the tree sorted by size, mark what you want gone, and move it to the
> Trash. Standalone build is a Finder-launchable `.app` bundle (`build.sh`), not a
> Windows `.exe`, and it is stdlib-only Python — no PyInstaller, no pip installs.

## Setup (one time)

Nothing to configure to start. Optional:

1. Open `config.json` and edit **excludes** — one path or folder-name per line
   (default: `/System /private /Volumes /dev /proc`; a trailing `/` means "everything under it")
2. Set **size** to `logical` (Finder's numbers) or `alloc` (sorts by what `du` reports)
3. Set **top_n** (how many rows `[Top N]` shows) and **trash** (blank = `~/.Trash`)
4. Rebuild the double-clickable app with `sh build.sh`

## Everyday use

1. Double-click **`Windirstat.app`**, or run `python3 windirstat.py` in a terminal
2. Pick the folder to scan (a window asks; pass a path to skip the dialog)
3. It scans all of it first, with a live count — then browse
4. Click a row to select it, **Up** to go to the parent folder, **Enter** to go into a folder
5. **Sort** cycles biggest-first, smallest-first, by name, most-files-first
6. **Mark** the rows you want deleted, then **Delete marked** moves them to the Trash
   and the totals drop the moment you mark, so you can see the space you would win back
7. **Top N** lists the biggest single files anywhere in the tree; **Search** finds
   rows by name anywhere (`.png` works as a search too)
8. The bottom of the window always shows the **total space the scanned folder uses**
   (logical and on-disk) plus that volume's used/free capacity, so you can see how
   much room you are winning back against the disk as a whole

Keyboard: `↑/↓` scroll, `←` up, `→`/`Enter` drill in, `s` sort, `m` mark, `d` delete,
`t` top, `/` search, `r` rescan, `q` quit.

**The list is a WinDirStat-style dropdown tree.** Folders carry a gutter marker:
`▸` means collapsed, `▾` expanded. Click the marker (or the name) to open that folder,
and its children appear indented directly under it; click again to close it. So you can
see the shape of the whole folder at once instead of only one level. Set
`"show_files": false` in `config.json` to see folders only, which keeps huge trees short.

**Right-click a row** for a small menu: Drill into, Drill up, Mark or unmark, Delete marked,
Top N, Search, Properties (size, path, kind, % of parent, file count), Close.
`Esc` or clicking the popup's background closes it.

## Files

| File | What it does |
|---|---|
| `windirstat.py` | The program you run every day: the scan, the snapshot, the browse window, and a `--print` text mode |
| `config.json` | Saved settings (excludes, which size drives sorting, top N, Trash path) |
| `build.sh` | Assembles `build/Windirstat.app` so you can double-click it from Finder |
| `launcher.c` | The launcher compiled into the bundle: Finder refuses to run a script placed there |
| `make_icon.py` | Draws the app icon (flat nested squares, like a disk map) and packs it with `iconutil` |
| `testdata/make_fixture.py` | Builds `testdata/tiny/`, an awkward tree with known numbers |
| `testdata/tiny/` | That fixture: a bundle, a hardlinked pair, a self-referencing symlink, a fifo, a locked folder |

## Notes

- **Two sizes per row.** `size (logical)` is `st_size` — what Finder and `ls -l` show.
  `on disk` is `st_blocks*512` — what `du` shows. APFS compression makes the second one
  smaller, so a compressed folder can look bigger in the first column than the space it
  really costs. Which one drives sorting is the `size` key in `config.json`.
- **The window is dark by design.** Rows alternate with a faint band so long lists read
  as lines, folders are brighter than files, the selected row gets a solid highlight,
  volumes and marked rows are the one accent colour. Nothing is grey-on-white.
- **Folder sizes are always ours.** macOS reports a folder's own entry size, not the
  total inside it, so every folder number here is summed from its files.
- **Symlinks count as 0 bytes and are never followed**, so a link back to a parent
  cannot loop the scan. Hardlinked files (same inode, `nlink > 1`) are counted **once**.
  APFS *clones* — files created by `cp -c` — have separate inodes, so cloned space is
  counted twice, same as `du` does.
- **Bundles** (`.app .photoslibrary .xcodeproj .pkg .component .bundle`) show their
  total size and their bytes are counted, but they are not browsed: they are packages,
  not folders. A plain folder someone named `thing.library` stays browsable.
- **Mount points stop the scan.** Another volume appears as a `^ other volume` row of
  0 bytes. The default excludes cover `/System` too, whose bytes live on the same
  device as `/`, so the name list is what keeps a home-folder scan from wandering there.
- **Denied folders do not stop a scan.** They are tallied as `N inaccessible` in the
  footer (`~/Library` corners are the usual cause).
- Big scans stop at 2,000,000 entries and say so in the footer; the numbers are partial.
- **What makes the bundle launchable:** `Contents/Info.plist` (a plain `Info.md` is
  ignored, so the first build did nothing when double-clicked) and a **native** launcher
  — Finder refuses to run a `#!/bin/sh` script placed inside an app bundle, so
  `build.sh` compiles `launcher.c`. Both mirror what a bundle that already works here does.
- The scan runs on its own thread and never touches the window; the window only reads a
  counter. Tk must be built on the main thread, so keeping them apart matters on macOS.

## Checking it works

| Step | Result |
|---|---|
| `python3 -m py_compile windirstat.py testdata/make_fixture.py` | PASS |
| `python3 -I testdata/make_fixture.py` | builds the fixture; `chmod 0` verified as denying listing |
| `python3 -I windirstat.py --print --depth 0 --top 3 testdata/tiny` | PASS — `44.3 KB` logical, `7 files`, `11 folders scanned`, `2 links`, `1 special`, `1 inaccessible`, `Foo.app` flagged `[] bundle` and not descended, hardlinked pair counted once |
| `python3 -I windirstat.py --print --alloc --top 3 --depth 0 testdata/tiny` | PASS — sorted by `on disk` (zero.bin 40.0 KB first) |
| disk-space footer numbers | PASS — text mode prints `volume: 926.3 GB total, 389.4 GB used, 537.0 GB free` while `df -h /System/Volumes/Data` says `926Gi / 361Gi / 537Gi`. Total and free match exactly; our **used** is `total − free`, so it also counts the ~28 GB APFS keeps reserved, which `df` puts in its free column instead. Same disk, two accounting choices - ours is the one that leaves no space uncounted |
| `python3 -I windirstat.py --print --depth 1 /Volumes` | PASS — `Macintosh HD` as `@link`, mounted volumes as `^volume` rows |
| `python3 -I windirstat.py --print --depth 1 /private/var/db/sudo` | PASS — `1 inaccessible`, no crash |
| `python3 -I testdata/smoke_gui.py` | PASS — the real window builds; all four sorts order the rows; drill, mark (root total drops by exactly the marked bytes), **delete into a temp Trash** (the file really leaves the tree and lands in the Trash folder, root total unchanged because it was already subtracted at mark time), and search by name all work; it then tears down with no traceback |
| `to_trash` into `/tmp/wtrash/.Trash-test`, twice | PASS — `big.txt`, then `big.txt 1` |
| `sh build.sh`, then run the launcher with no tty | PASS — exit 0, output landed in `windirstat.log`, launcher is a native arm64 binary, mode 0755, logs `exited rc=0` |
| `open build/Windirstat.app` (how Finder runs it) | PASS — the launcher ran within seconds (its line is in the log) and a live `windirstat.py` process was sitting on the "Folder to scan" dialog |
| `python3 -I windirstat.py --print --depth 2 ~/Downloads` | PASS — instant, 4 files |
| `python3 -I windirstat.py --print --depth 1 ~/Library` | PASS — `49.0 GB`, `109,948 files`, `28,047 folders scanned`, `700 inaccessible`, `39.3s` |
| rebuilding the fixture twice | PASS — a mode-000 folder is unlocked before `rmtree`, so reruns work |

Ran on this machine 2026-11-23, arm64, CPython 3.13.0. Not exercised here: the cross-volume
Trash copy (needs a second volume; it warns before copying), and **how the window looks** —
`screencapture` could not reach a display in this environment, so the rows were checked by
text mode and by the smoke test, not by eye. Open `Windirstat.app` to judge the layout.
