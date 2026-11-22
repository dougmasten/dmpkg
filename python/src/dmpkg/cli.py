"""
dmpkg - project image packer for the CoCo SDC STREAM protocol.

Usage:
  dmpkg build project.yaml [-v]
  dmpkg list  IMAGE [--map [--names REPORT]]
  dmpkg verify IMAGE
  dmpkg tokenize PROGRAM.TXT OUT.BAS [--base ADDR] [--uppercase]
  dmpkg detokenize PROGRAM.BAS [OUT.TXT]

(or python -m dmpkg ...)
"""
from __future__ import annotations

import argparse
import sys
import zlib

from . import __version__
from .decb import (DIR_HI, DIR_LO, DIR_TRACK, DISK_BYTES, GRAN_BYTES, GRANULES, HD_MIN,
                   SS_MAX, TRACK_BYTES, gran_of, read_disk)
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
