"""
dmpkg - project disc packer for the CoCo SDC STREAM protocol.

Usage:
  dmpkg build project.yaml [-v]
  dmpkg list  IMAGE
  dmpkg verify IMAGE

(or python -m dmpkg ...)
"""
from __future__ import annotations

import argparse
import sys
import zlib

from . import __version__
from .decb import DIR_HI, DIR_LO, DISK_BYTES, HD_MIN, SS_MAX, gran_of, read_disk
from .errors import PackError
from .image import (BLOCK, F_DECB, F_PHYS, H_DISK, HDR_SIZE, MIN_IMAGE, TOC_ENTRY,
                    read_image)
from .pack import build


def cmd_list(path):
    img, h, ents = read_image(path)
    print(f"{h['name']}  v{h['version']}  build ${h['build_id']:08X}  "
          f"{h['blocks']} blocks  TOC {h['toc_blocks']}"
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
    holding header, TOC or asset bytes owned by DMPKG.DAT and nothing else."""
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
    runs = [(0, h["toc_blocks"] * BLOCK)] + [(e["block"] * BLOCK + e["off"], e["length"])
                                              for e in ents]
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
            if owner.get(g) != "DMPKG.DAT":
                print(f"{what}: granule {g} not owned by DMPKG.DAT")
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
    sub.add_parser("list", help="show an image's TOC").add_argument("image")
    sub.add_parser("verify", help="check an image's CRCs").add_argument("image")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "build":
            build(args.manifest, args.verbose, loader=not args.no_loader)
        elif args.cmd == "list":
            cmd_list(args.image)
        else:
            return 0 if cmd_verify(args.image) else 1
    except PackError as e:
        print(f"dmpkg: error: {e}", file=sys.stderr)
        return 1
    return 0
