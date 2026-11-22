"""
dmpkg - project image packer for the CoCo SDC STREAM protocol.

Usage:
  dmpkg build project.yaml [-v]
  dmpkg list  IMAGE [--map [--names REPORT]]
  dmpkg verify IMAGE
  dmpkg extract IMAGE NAME [-o OUT] [--names REPORT]
  dmpkg decb dir DISK[,]
  dmpkg decb copy DISK,FILE [DISK,FILE...] TARGET
  dmpkg decb free DISK
  dmpkg decb fstat DISK,FILE | FILE.BIN
  dmpkg decb list [-t] DISK,FILE
  dmpkg tokenize PROGRAM.TXT OUT.BAS [--base ADDR] [--uppercase]
  dmpkg detokenize PROGRAM.BAS [OUT.TXT]

(or python -m dmpkg ...)
"""
from __future__ import annotations

import argparse
import os
import sys
import zlib

from . import __version__
from .decb import (DIR_HI, DIR_LO, DIR_TRACK, DISK_BYTES, GRAN_BYTES, GRANULES,
                   HD_MIN, SS_MAX, TRACK_BYTES, gran_of, loadm, parse_loadm, read_disk,
                   read_files)
from .errors import PackError
from .image import (BLOCK, F_DECB, F_PHYS, H_DISK, HDR_SIZE, MIN_IMAGE, TOC_ENTRY,
                    read_image, toc_size)
from . import basic
from .pack import build


def cmd_list(path, show_map=False, names_from=None):
    img, h, ents = read_image(path)
    if show_map:
        if not h["flags"] & H_DISK:
            raise PackError(f"{path} is a stream image, not a DECB disk: no disk map")
        names = report_names(names_from) if names_from else []
        print(f"{h['title']}  build ${h['build_id']:08X}  {path}")
        print()
        print("\n".join(disk_map(img, h, ents, names)))
        return
    print(f"{h['title']}  format {h['format']}  build ${h['build_id']:08X}  "
          f"{h['blocks']} blocks  TOC {toc_size(h['count'])[1]}"
          + ("  DECB disk" if h["flags"] & H_DISK else ""))
    if h["flags"] & H_DISK:
        for f, grans in read_disk(img):
            print(f"  {f:<12} {len(grans):>2} granule(s)")
    names = [",".join(n for n, b in (("phys", F_PHYS), ("decb", F_DECB)) if e["flags"] & b) or "-"
             for e in ents]
    fw = max([5] + [len(n) for n in names])
    print()
    print(f"  {'idx':>3} {'block':>7} {'off':>5} {'length':>8}  {'dest':<7}  {'flags':<{fw}}  crc32")
    for i, (e, fl) in enumerate(zip(ents, names)):
        print(f"  {i:>3} {e['block']:>7} {e['off']:>5} {e['length']:>8}  "
              f"${e['dest']:06X}  {fl:<{fw}}  ${e['crc']:08X}")


def report_names(path) -> list[str]:
    """Asset names, in TOC order, from a build's map report."""
    names = []
    with open(path) as f:
        for line in f:
            p = line.split()
            if len(p) >= 3 and p[0].isdigit() and p[2].isdigit() and int(p[0]) == len(names):
                names.append(p[1])
    return names


def disk_map(img: bytes, h: dict, ents: list, names: list[str]) -> list[str]:
    """Track by track: each granule's DECB file, and the assets with bytes
    on the track; then the assets past track 34."""
    owner = {g: f for f, grans in read_disk(img) for g in grans}
    label = [names[i] if i < len(names) else f"#{i}" for i in range(len(ents))]
    spans = [(0, toc_size(h["count"])[1] * BLOCK, "header+TOC")] + [
        (e["block"] * BLOCK + e["off"], e["length"], label[i]) for i, e in enumerate(ents)]
    fw = max([12] + [len(f) for f in owner.values()])
    out = [f"{'track':>5}  {'granules':<{2 * fw + 7}}  data"]
    g = 0
    for t in range(DISK_BYTES // TRACK_BYTES):
        lo, hi = t * TRACK_BYTES, (t + 1) * TRACK_BYTES
        if t == DIR_TRACK:
            out.append(f"{t:>5}  [ directory and FAT ]")
            continue
        cells = []
        for gi in (g, g + 1):
            cells.append(f"{gi:>2}:{owner.get(gi, '-'):<{fw}}")
        g += 2
        on = [n for pos, ln, n in spans if ln and pos < hi and pos + ln > lo]
        out.append(f"{t:>5}  {cells[0]} {cells[1]}  {', '.join(on)}".rstrip())
    past = [n for pos, ln, n in spans if pos + ln > DISK_BYTES]
    used = max(pos + ln for pos, ln, _ in spans)
    free = GRANULES - len(owner)
    out.append("")
    out.append(f"{free} granule(s) free ({GRAN_BYTES} bytes each)")
    if past:
        out.append(f"past track 34 (Disk BASIC never looks): {', '.join(past)}")
    out.append(f"data ends at byte {used}; the file is {len(img)} bytes")
    return out


def cmd_extract(path, which, out=None, names_from=None):
    """Write one asset back out, after checking it against its CRC. Raw
    assets come out as stored; decb assets as a LOADM file again."""
    img, h, ents = read_image(path)
    if names_from is None:
        guess = os.path.splitext(path)[0] + ".map"   # the build's map report, if next to it
        names_from = guess if os.path.exists(guess) else None
    names = report_names(names_from) if names_from else []
    key = which[1:] if which.startswith("#") else which
    if key.isdigit():
        i = int(key)
        if i >= len(ents):
            raise PackError(f"{path}: no asset {i} (the image has {len(ents)})")
    else:
        hit = [n for n, name in enumerate(names) if name.upper() == which.upper()]
        if not hit:
            where = (f"in {names_from}" if names_from else
                     "(no map report: give --names REPORT, or the asset's index)")
            raise PackError(f"{path}: no asset named {which} {where}")
        i = hit[0]
    e = ents[i]
    label = names[i] if i < len(names) else f"#{i}"
    p = e["block"] * BLOCK + e["off"]
    data = img[p:p + e["length"]]
    if len(data) != e["length"] or zlib.crc32(data) != e["crc"]:
        raise PackError(f"{path}: asset {label}: CRC mismatch, not written")
    decb = bool(e["flags"] & F_DECB)
    if decb:
        data = loadm(data, f"{path}: asset {label}")
    if out is None:
        out = (names[i] if i < len(names) else f"asset{i}") + ".bin"
    if out == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(out, "wb") as f:
            f.write(data)
        print(f"{label}: {len(data)} bytes{' (LOADM file)' if decb else ''} -> {out}")


def read_bytes(path) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise PackError(f"{path}: {e.strerror}")


def disk_path(arg: str) -> tuple[str, str | None]:
    """Toolshed's DISK,FILE: (disk, FILE) or (disk, None) for "DISK," or "DISK"."""
    disk, _, name = arg.partition(",")
    return disk, (name or None)


def disk_file(arg: str) -> dict:
    """The file named by DISK,FILE, from read_files."""
    disk, name = disk_path(arg)
    if not name:
        raise PackError(f"{arg}: give a file on the disk as DISK,FILE")
    for f in read_files(read_bytes(disk), disk):
        if f["name"].upper() == name.upper():
            return f
    raise PackError(f"{disk}: no {name.upper()} on the disk")


def cmd_decb_dir(arg):
    """The directory, laid out like Toolshed's decb dir: name, extension,
    file type, A(SCII) or B(inary), granules."""
    disk, _ = disk_path(arg)
    print(f"Directory of: {disk},")
    print()
    for f in read_files(read_bytes(disk), disk):
        name, _, ext = f["name"].partition(".")
        print(f"{name:<8} {ext:<3}  {f['type']}  {'A' if f['ascii'] else 'B'}  {len(f['grans'])}")


def cmd_decb_free(disk):
    used = sum(len(g) for _, g in read_disk(read_bytes(disk)))
    free = GRANULES - used
    print(f"Free granules: {free} ({free * GRAN_BYTES} bytes)")


def cmd_decb_fstat(arg):
    """A disk file's directory entry and FAT chain, like Toolshed's decb fstat,
    and for a LOADM file its segments and exec address. A native LOADM file
    (no comma) gets just the segments."""
    disk, name = disk_path(arg)
    if name:
        f = disk_file(arg)
        data, src = f["data"], arg
        kinds = {0: "BASIC Program", 1: "Data", 2: "M/L Program", 3: "Text Editor Source"}
        print(f"File Information for {disk},{f['name']}")
        print(f"  File type          : {kinds.get(f['type'], '$%02X' % f['type'])}")
        print(f"  Data type          : {'ASCII' if f['ascii'] else 'Binary'}")
        print(f"  File size          : {len(data)} bytes")
        print(f"  First granule      : {f['grans'][0]}")
        print(f"  Last sector        : {f['last']} bytes")
        sizes = [GRAN_BYTES] * (len(f["grans"]) - 1) + [len(data) - GRAN_BYTES * (len(f["grans"]) - 1)]
        print(f"  FAT chain          : {''.join(f'[{g}:{n}] ' for g, n in zip(f['grans'], sizes))}")
        if f["type"] != 2 or f["ascii"]:
            return
    else:
        data, src = read_bytes(disk), disk
        print(f"File Information for {disk}")
    segs, exe = parse_loadm(data, src)
    for addr, d in segs:
        print(f"  Segment            : ${addr:04X}-${addr + len(d) - 1:04X} ({len(d)} bytes)")
    print(f"  Exec address       : ${exe:04X}")


def cmd_decb_copy(srcs, target):
    """Copy files off DECB disks (DISK,FILE) to a native file or directory."""
    to_dir = os.path.isdir(target) or target.endswith(os.sep) or len(srcs) > 1
    if to_dir:
        os.makedirs(target, exist_ok=True)
    for src in srcs:
        f = disk_file(src)
        out = os.path.join(target, f["name"]) if to_dir else target
        with open(out, "wb") as fh:
            fh.write(f["data"])
        print(f"{src} -> {out} ({len(f['data'])} bytes)")


def cmd_decb_list(arg, tokens=False):
    """A text file, or with -t a tokenized BASIC program, as text."""
    f = disk_file(arg)
    if tokens:
        sys.stdout.write(basic.detokenize(f["data"]))
    else:
        sys.stdout.write(f["data"].decode("latin-1").replace("\r", "\n"))


def cmd_verify(path) -> bool:
    img, h, ents = read_image(path)
    ok = len(img) == h["blocks"] * BLOCK and len(img) % BLOCK == 0 and len(img) >= MIN_IMAGE
    if not ok:
        print(f"size mismatch or under {MIN_IMAGE} bytes: {len(img)} bytes, "
              f"header says {h['blocks']} blocks")
    for i, e in enumerate(ents):
        p = e["block"] * BLOCK + e["off"]
        if zlib.crc32(img[p:p + e["length"]]) != e["crc"]:
            print(f"asset {i}: CRC mismatch")
            ok = False
    toc = img[HDR_SIZE:HDR_SIZE + TOC_ENTRY * h["count"]]
    if zlib.crc32(toc) != h["build_id"]:
        print("build id mismatch")
        ok = False
    if h["flags"] & H_DISK:
        ok &= verify_disk(img, h, ents)
    print("OK" if ok else "FAILED")
    return ok


def verify_disk(img: bytes, h: dict, ents: list) -> bool:
    """Disk checks: SDC geometry, no asset on track 17, and every granule
    holding header, TOC or asset bytes owned by the data file (the one that
    owns granule 0, where the header is) and nothing else."""
    ok = True
    if len(img) < DISK_BYTES or SS_MAX < len(img) < HD_MIN:
        print(f"disk size {len(img)}: the SDC would not see 35 single-sided tracks")
        ok = False
    try:
        files = dict(read_disk(img))
    except PackError as e:
        print(e)
        return False
    owner = {}
    for f, grans in files.items():
        for g in grans:
            if g in owner:
                print(f"granule {g} in both {owner[g]} and {f}")
                ok = False
            owner[g] = f
    data = owner.get(0)
    if data is None:
        print("granule 0 (header) not owned by any file")
        return False
    runs = [(0, toc_size(h["count"])[1] * BLOCK)] + [
        (e["block"] * BLOCK + e["off"], e["length"]) for e in ents]
    for i, (pos, n) in enumerate(runs):
        stop = min(pos + n, DISK_BYTES)
        if pos >= stop:
            continue
        what = "TOC" if i == 0 else f"asset {i - 1}"
        if pos < DIR_HI and stop > DIR_LO:
            print(f"{what} overlaps track 17")
            ok = False
            continue
        for g in range(gran_of(pos), gran_of(stop - 1) + 1):
            if owner.get(g) != data:
                print(f"{what}: granule {g} not owned by {data}")
                ok = False
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dmpkg", description=__doc__.split("\n")[1])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="pack a project from a YAML manifest")
    b.add_argument("manifest")
    b.add_argument("-v", "--verbose", action="store_true")
    b.add_argument("--no-loader", action="store_true",
                   help="leave the disk's loader off (first pass of a two-pass build)")
    li = sub.add_parser("list", help="show an image's TOC (or, with --map, a disk map)")
    li.add_argument("image")
    li.add_argument("--map", action="store_true",
                    help="track-by-track map of a disk image: granule owners, assets")
    li.add_argument("--names", metavar="REPORT",
                    help="label assets with the names in a build's map report "
                         "(default: #index)")
    sub.add_parser("verify", help="check an image's CRCs").add_argument("image")
    x = sub.add_parser("extract", help="write one asset back out, checked against its CRC")
    x.add_argument("image")
    x.add_argument("name", help="asset name (from the map report) or index (3 or #3)")
    x.add_argument("-o", "--output", help="file to write, - for stdout (default: NAME.bin)")
    x.add_argument("--names", metavar="REPORT",
                   help="the build's map report, for asset names "
                        "(default: IMAGE with .map, if it exists)")
    dc = sub.add_parser("decb", help="read DECB disks, with Toolshed's decb commands "
                                     "and DISK,FILE paths (read-only)")
    dsub = dc.add_subparsers(dest="decb_cmd", required=True)
    dsub.add_parser("dir", help="list the files on a disk").add_argument("disk", metavar="DISK[,]")
    cp = dsub.add_parser("copy", help="copy files off a disk to a native file or directory")
    cp.add_argument("src", nargs="+", metavar="DISK,FILE")
    cp.add_argument("target", metavar="TARGET")
    dsub.add_parser("free", help="free granules on a disk").add_argument("disk")
    dsub.add_parser("fstat", help="a file's details, and a LOADM file's segments") \
        .add_argument("file", metavar="DISK,FILE|FILE.BIN")
    ls = dsub.add_parser("list", help="show a text file, or with -t a BASIC program")
    ls.add_argument("-t", action="store_true", help="BASIC token translation")
    ls.add_argument("file", metavar="DISK,FILE")
    t = sub.add_parser("tokenize", help="ASCII BASIC program -> tokenized .BAS file")
    t.add_argument("source")
    t.add_argument("output")
    t.add_argument("--base", type=lambda s: int(s, 0), default=basic.BASE,
                   help="address the line links are made for (default 0x2601)")
    t.add_argument("--uppercase", action="store_true",
                   help="upper-case the program outside quotes, REM, ' and DATA")
    d = sub.add_parser("detokenize", help="tokenized .BAS file -> ASCII listing")
    d.add_argument("program")
    d.add_argument("output", nargs="?", help="default: print it")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "build":
            build(args.manifest, args.verbose, loader=not args.no_loader)
        elif args.cmd == "list":
            cmd_list(args.image, args.map, args.names)
        elif args.cmd == "extract":
            cmd_extract(args.image, args.name, args.output, args.names)
        elif args.cmd == "decb":
            {"dir": lambda: cmd_decb_dir(args.disk),
             "copy": lambda: cmd_decb_copy(args.src, args.target),
             "free": lambda: cmd_decb_free(args.disk),
             "fstat": lambda: cmd_decb_fstat(args.file),
             "list": lambda: cmd_decb_list(args.file, args.t)}[args.decb_cmd]()
        elif args.cmd == "tokenize":
            with open(args.source, encoding="latin-1") as f:
                text = f.read()
            prog = basic.tokenize(text, args.base, args.uppercase)
            lower = [] if args.uppercase else basic.lowercase_lines(text)
            if lower:
                many = len(lower) > 1
                nums = ", ".join(map(str, lower[:10]))
                more = f" (+{len(lower) - 10} more)" if len(lower) > 10 else ""
                print(f"dmpkg: warning: {args.source}: lower case outside quotes, REM and DATA "
                      f"in line{'s' if many else ''} {nums}{more}: keywords are upper case only, "
                      f"so {'these lines' if many else 'it'} will give ?SN ERROR "
                      f"(--uppercase to fix)", file=sys.stderr)
            with open(args.output, "wb") as f:
                f.write(prog)
        elif args.cmd == "detokenize":
            with open(args.program, "rb") as f:
                text = basic.detokenize(f.read())
            if args.output:
                with open(args.output, "w", encoding="latin-1") as f:
                    f.write(text)
            else:
                sys.stdout.write(text)
        else:
            return 0 if cmd_verify(args.image) else 1
    except PackError as e:
        print(f"dmpkg: error: {e}", file=sys.stderr)
        return 1
    return 0
