"""
Packing and image reading for dmpkg: builds a project's image and include
files from its manifest, and reads images back. The command line is in
dmpkg.cli.

The image is read by a CoCo program with the CoCo SDC STREAM command ($9x):
a 24-bit 512-byte block number goes in $FF49-$FF4B, and data comes 16 bits
at a time from $FF4A/$FF4B with READY polled between blocks.

With a disk: section the image is also a 35-track DECB disk: header, TOC
and assets sit in granules owned by DMPKG.DAT, next to a LOADM loader (and an
optional AUTOEXEC.BAS), so one file holds both the program and its assets.

Outputs of build() (paths from the manifest, relative to it):
  image    stream image, padded to 512-byte blocks and the SDC minimum size
  equates  lwasm include: EQUs only (no bytes emitted)
  table    lwasm include: mount name, build_id and 12-byte asset descriptors
  map      human-readable layout report
"""
from __future__ import annotations

import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field

import yaml

BLOCK = 512
MIN_IMAGE = 0x14400          # smallest DSK that M: mounts, per the SDC User Guide
                             # (324 sectors = 82,944 bytes = 162 blocks)
MAX_BLOCKS = 1 << 24         # 24-bit block number
MAGIC = b"DMPK"
VERSION = 1
HDR_SIZE = 32
TOC_ENTRY = 16

F_PHYS = 0x80                # dest is a CoCo 3 physical address (MMU-windowed load)
F_DECB = 0x40                # stored as segment records, loader follows them

H_DISK = 0x01                # header flags: the image is also a DECB disk

# DECB disk: 35 tracks x 18 sectors x 256 bytes, single sided. Track 17 holds
# the FAT (sector 2) and directory (sectors 3-11); the other 34 tracks are 68
# granules of 9 sectors, two per track.
SECTOR = 256
TRACK_BYTES = 18 * SECTOR    # 4,608
GRAN_BYTES = 9 * SECTOR      # 2,304
GRANULES = 68
DIR_TRACK = 17
DISK_BYTES = 35 * TRACK_BYTES        # 161,280 = 315 blocks
DIR_LO = DIR_TRACK * TRACK_BYTES     # 78,336: track 17 starts (block 153)
DIR_HI = DIR_LO + TRACK_BYTES        # 82,944: track 18 starts (block 162)
SS_MAX = 720 * SECTOR        # the SDC treats 721-2880 sectors as double sided,
HD_MIN = 2882 * SECTOR       # which moves track 17; past 2880 it's a hard disk
                             # image seen as single sided (2882: whole blocks)


class PackError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def num(v, what: str) -> int:
    """Accept YAML ints or strings like $4000, 0x4000, &H4000, %1010, 1234."""
    if isinstance(v, bool):
        raise PackError(f"{what}: expected a number, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace("_", "")
        try:
            if s.startswith("$"):
                return int(s[1:], 16)
            if s[:2].lower() == "&h":
                return int(s[2:], 16)
            if s.startswith("%"):
                return int(s[1:], 2)
            return int(s, 0)
        except ValueError:
            pass
    raise PackError(f"{what}: can't read {v!r} as a number")


def sym(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", name).upper()
    if not s or s[0].isdigit():
        s = "_" + s
    return s


def sdc_name(path: str) -> str:
    """'GAMES/mygame.sds' -> 'GAMES/MYGAME.SDS' (SDC path: 8.3 names, upper-cased)."""
    path = path.replace("\\", "/").strip("/")
    *dirs, leaf = path.split("/")
    base, _, ext = leaf.rpartition(".")
    if not base:
        base, ext = ext, ""
    if len(base) > 8 or len(ext) > 3 or not base:
        raise PackError(f"sdc_path: '{leaf}' is not a valid 8.3 name")
    for d in dirs:
        if len(d) > 8:
            raise PackError(f"sdc_path: directory '{d}' longer than 8 characters")
    leaf = base + ("." + ext if ext else "")
    out = "/".join(dirs + [leaf]).upper()
    if len(out) + 2 > 255:
        raise PackError("sdc_path too long")
    return out


def gran_offset(g: int) -> int:
    """Byte offset of granule g in the disk image (granules skip track 17)."""
    t = g // 2
    t += t >= DIR_TRACK
    return t * TRACK_BYTES + (g % 2) * GRAN_BYTES


def gran_of(pos: int) -> int:
    """Granule holding byte pos (pos must be in the disk area, not track 17)."""
    t = pos // TRACK_BYTES
    return (t - (t > DIR_TRACK)) * 2 + (pos % TRACK_BYTES) // GRAN_BYTES


def first_fit(free: list, n: int, align: bool) -> int:
    """Take n bytes from the first free [lo, hi) range they fit in (hi None:
    open-ended), block-aligned if asked. The leftover pieces stay free."""
    for i, (lo, hi) in enumerate(free):
        p = -(-lo // BLOCK) * BLOCK if align else lo
        if hi is None or p + n <= hi:
            rest = [(lo, p)] if p > lo else []
            if hi is None or p + n < hi:
                rest.append((p + n, hi))
            free[i:i + 1] = rest
            return p
    raise AssertionError("the last free range is open-ended")


def decb_name(path: str, what: str) -> tuple[str, str]:
    """'build/loader.bin' -> ('LOADER', 'BIN'), checked as a DECB 8.3 name."""
    leaf = os.path.basename(path).upper()
    base, _, ext = leaf.rpartition(".")
    if not base:
        base, ext = ext, ""
    if not re.fullmatch(r"[A-Z0-9_$#!&@%^()'{}~-]{1,8}", base) or len(ext) > 3 \
            or not re.fullmatch(r"[A-Z0-9_$#!&@%^()'{}~-]*", ext):
        raise PackError(f"{what}: '{leaf}' is not a valid DECB 8.3 name")
    return base, ext


def parse_decb(raw: bytes, src: str):
    """Parse a DECB LOADM binary into (segments, exec)."""
    segs, i = [], 0
    while True:
        if i >= len(raw):
            raise PackError(f"{src}: DECB file ends without a $FF postamble")
        kind = raw[i]
        if kind == 0x00:
            if i + 5 > len(raw):
                raise PackError(f"{src}: truncated DECB segment header at {i}")
            ln, addr = struct.unpack_from(">HH", raw, i + 1)
            data = raw[i + 5:i + 5 + ln]
            if len(data) != ln:
                raise PackError(f"{src}: truncated DECB segment at {i}")
            if ln:
                segs.append((addr, data))
            i += 5 + ln
        elif kind == 0xFF:
            if i + 5 > len(raw):
                raise PackError(f"{src}: truncated DECB postamble")
            _, exe = struct.unpack_from(">HH", raw, i + 1)
            return segs, exe
        else:
            raise PackError(f"{src}: bad DECB record type ${kind:02X} at offset {i}")


def decb_records(segs, exe) -> bytes:
    """Stored form: [len.w addr.w data]... [0000 exec.w] - read by sdc_load."""
    out = bytearray()
    for addr, data in segs:
        if len(data) > 0xFFFF:
            raise PackError("DECB segment over 64K")
        out += struct.pack(">HH", len(data), addr) + data
    out += struct.pack(">HH", 0, exe)
    return bytes(out)


# ---------------------------------------------------------------- model

@dataclass
class Asset:
    name: str
    src: str
    kind: str                 # raw | decb
    dest: int = 0
    phys: bool = False
    align: bool = False       # start on a fresh 512-byte block
    data: bytes = b""
    exec_addr: int | None = None
    segs: list = field(default_factory=list)
    block: int = 0
    off: int = 0

    @property
    def flags(self) -> int:
        f = F_PHYS if self.phys else 0
        if self.kind == "decb":
            f |= F_DECB
        return f

    def descriptor(self) -> bytes:
        return (self.block.to_bytes(3, "big") + self.off.to_bytes(2, "big")
                + len(self.data).to_bytes(3, "big") + bytes([self.flags])
                + self.dest.to_bytes(3, "big"))


# ---------------------------------------------------------------- build

KNOWN_PROJECT = {"name", "image", "sdc_path", "equates", "table", "map", "prefix",
                 "min_size", "pad_byte", "disk"}
KNOWN_DISK = {"loader", "autoexec"}
KNOWN_ASSET = {"name", "file", "type", "load", "phys", "align"}


def load_manifest(path: str):
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or "project" not in doc or "assets" not in doc:
        raise PackError(f"{path}: needs top-level 'project' and 'assets' keys")
    proj, assets = doc["project"], doc["assets"]
    if not isinstance(proj, dict):
        raise PackError("project: must be a mapping")
    if not isinstance(assets, list) or not assets:
        raise PackError("assets: must be a non-empty list")
    bad = set(proj) - KNOWN_PROJECT
    if bad:
        raise PackError(f"project: unknown key(s) {sorted(bad)}")
    return proj, assets


def write_disk(img: bytearray, used: list, files: list) -> list:
    """Make the first 35 tracks of img a DECB disk. used holds (pos, length)
    runs of dmpkg data (header, TOC, assets); every granule they touch goes
    to DMPKG.DAT, so Disk BASIC never hands it out. files holds (name, ext,
    bytes) put in the granules left over. Returns [(file, granules)]."""
    fat = bytearray([0xFF]) * GRANULES          # $FF: free
    reach = {}                                  # granule -> bytes used from its start
    for pos, n in used:
        stop = min(pos + n, DISK_BYTES)
        if pos >= stop:
            continue                            # past track 34
        for g in range(gran_of(pos), gran_of(stop - 1) + 1):
            reach[g] = max(reach.get(g, 0), min(stop - gran_offset(g), GRAN_BYTES))

    def chain(grans, tail):
        """Link grans in the FAT; tail = bytes in the last one. Returns the
        bytes used in its last sector (the directory's last-sector count)."""
        for a, b in zip(grans, grans[1:]):
            fat[a] = b
        sectors = -(-tail // SECTOR)
        fat[grans[-1]] = 0xC0 + sectors
        return tail - (sectors - 1) * SECTOR

    dat = sorted(reach)
    entries = [("DMPKG", "DAT", 1, False, dat[0], chain(dat, reach[dat[-1]]))]
    out = [("DMPKG.DAT", len(dat))]
    for name, ext, data in files:
        free = [g for g in range(GRANULES) if fat[g] == 0xFF]
        need = -(-len(data) // GRAN_BYTES)
        if need > len(free):
            raise PackError(f"disk full: {name}.{ext} needs {need} granule(s), "
                            f"{len(free)} left after the assets")
        grans = free[:need]
        for i, g in enumerate(grans):
            part = data[i * GRAN_BYTES:(i + 1) * GRAN_BYTES]
            img[gran_offset(g):gran_offset(g) + len(part)] = part
        basic = ext == "BAS"                    # ASCII BASIC program, else LOADM file
        entries.append((name, ext, 0 if basic else 2, basic, grans[0],
                        chain(grans, len(data) - (need - 1) * GRAN_BYTES)))
        out.append((name + ("." + ext if ext else ""), need))

    # track 17: FAT in sector 2 (68 entries, rest $00), directory in sectors
    # 3-11 (32-byte entries, $FF = never used), everything else $FF
    img[DIR_LO:DIR_HI] = bytes([0xFF]) * TRACK_BYTES
    img[DIR_LO + SECTOR:DIR_LO + 2 * SECTOR] = fat + bytes(SECTOR - GRANULES)
    d = DIR_LO + 2 * SECTOR
    for name, ext, ftype, ascii_, first, last in entries:
        img[d:d + 32] = (name.ljust(8).encode() + ext.ljust(3).encode()
                         + bytes([ftype, 0xFF if ascii_ else 0x00, first])
                         + last.to_bytes(2, "big") + bytes(16))
        d += 32
    return out


def build(manifest: str, verbose=False) -> dict:
    base = os.path.dirname(os.path.abspath(manifest))
    proj, raw_assets = load_manifest(manifest)
    rel = lambda p: os.path.join(base, p)

    def out(p):
        """Output path relative to the manifest, its folder created."""
        path = rel(p)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return path

    name = str(proj.get("name", "PROJECT"))
    image = proj.get("image")
    if not image:
        raise PackError("project.image is required (e.g. MYGAME.SDS)")
    disk = proj.get("disk", False)
    if disk is True:
        disk = {}
    if disk is not False and not isinstance(disk, dict):
        raise PackError("project.disk: must be true, false or a mapping")
    if isinstance(disk, dict):
        bad = set(disk) - KNOWN_DISK
        if bad:
            raise PackError(f"project.disk: unknown key(s) {sorted(bad)}")
    # a disk image is normally already mounted (the loader came off it), so
    # it has no mount name unless sdc_path asks for one
    if "sdc_path" in proj or disk is False:
        mount = sdc_name(str(proj.get("sdc_path", os.path.basename(image))))
    else:
        mount = None
    prefix = str(proj.get("prefix", ""))
    min_size = num(proj.get("min_size", MIN_IMAGE), "project.min_size")
    if min_size < MIN_IMAGE:
        raise PackError(f"project.min_size ${min_size:X} is below ${MIN_IMAGE:X}: "
                        "the SDC won't mount a smaller image")
    pad_byte = num(proj.get("pad_byte", 0 if disk is False else 0xFF),
                   "project.pad_byte") & 0xFF

    loader = None                     # (disk name, ext, bytes) of the LOADM file
    autoexec = False
    if disk is not False:
        if "loader" in disk:
            lpath = str(disk["loader"])
            lname = decb_name(lpath, "project.disk.loader")
            try:
                with open(rel(lpath), "rb") as f:
                    raw = f.read()
            except FileNotFoundError:
                # first pass of a two-pass build: the loader is assembled
                # against the include files this run writes
                print(f"dmpkg: note: {lpath} not found yet; disk written without it",
                      file=sys.stderr)
            except OSError as e:
                raise PackError(f"project.disk.loader: {e}")
            else:
                parse_decb(raw, lpath)            # must be a LOADM file
                loader = (*lname, raw)
        autoexec = disk.get("autoexec", "loader" in disk)
        if not isinstance(autoexec, bool):
            raise PackError("project.disk.autoexec must be true or false")
        if autoexec and "loader" not in disk:
            raise PackError("project.disk.autoexec needs a loader to run")

    assets: list[Asset] = []
    seen = set()
    for i, a in enumerate(raw_assets):
        where = f"assets[{i}]"
        if not isinstance(a, dict):
            raise PackError(f"{where}: must be a mapping")
        bad = set(a) - KNOWN_ASSET
        if bad:
            raise PackError(f"{where}: unknown key(s) {sorted(bad)}")
        if "name" not in a or "file" not in a:
            raise PackError(f"{where}: 'name' and 'file' are required")
        nm = sym(str(a["name"]))
        if nm in seen:
            raise PackError(f"{where}: duplicate asset name {nm}")
        seen.add(nm)
        where = f"asset {nm}"
        kind = str(a.get("type", "raw")).lower()
        if kind not in ("raw", "decb"):
            raise PackError(f"{where}: type must be raw or decb")
        if "load" in a and "phys" in a:
            raise PackError(f"{where}: give either load or phys, not both")
        align = a.get("align", False)
        if not isinstance(align, bool):
            raise PackError(f"{where}: align must be true or false")
        src = rel(str(a["file"]))
        try:
            with open(src, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise PackError(f"{where}: {e}")

        asset = Asset(name=nm, src=str(a["file"]), kind=kind,
                      align=align)
        if kind == "decb":
            if "load" in a or "phys" in a:
                raise PackError(f"{where}: decb assets load to their own segment addresses")
            asset.segs, asset.exec_addr = parse_decb(raw, asset.src)
            asset.data = decb_records(asset.segs, asset.exec_addr)
        else:
            if not raw:
                raise PackError(f"{where}: file is empty")
            asset.data = raw
            if "phys" in a:
                asset.phys = True
                asset.dest = num(a["phys"], f"{where}.phys")
                if asset.dest + len(asset.data) > 0x200000:
                    raise PackError(f"{where}: phys ${asset.dest:06X}+{len(asset.data)} "
                                    "runs past 2MB")
            elif "load" in a:
                asset.dest = num(a["load"], f"{where}.load")
                if asset.dest + len(asset.data) > 0x10000:
                    raise PackError(f"{where}: load ${asset.dest:04X}+{len(asset.data)} "
                                    "runs past $FFFF (use phys: for CoCo 3 RAM)")
            else:
                raise PackError(f"{where}: raw assets need load: or phys:")
        if len(asset.data) >= 1 << 24:
            raise PackError(f"{where}: over 16MB")
        assets.append(asset)

    # ---- layout: header + TOC at block 0, then assets first-fit in manifest
    # order: each takes the first free space it fits, so a small asset can
    # fill an align gap (or, on a disk, the space before track 17) left
    # earlier. A stream can't jump track 17, so on a disk no asset crosses it.
    toc_bytes = HDR_SIZE + TOC_ENTRY * len(assets)
    toc_blocks = -(-toc_bytes // BLOCK)
    if toc_blocks > 255:
        raise PackError("too many assets for the TOC")
    data0 = toc_blocks * BLOCK
    if disk is False:
        free = [(data0, None)]
    else:
        if data0 > DIR_LO:
            raise PackError("TOC runs into the DECB directory track")
        free = [(data0, DIR_LO), (DIR_HI, None)]
    for a in assets:
        a.block, a.off = divmod(first_fit(free, len(a.data), a.align), BLOCK)
    end = max(a.block * BLOCK + a.off + len(a.data) for a in assets)
    size = max(-(-end // BLOCK) * BLOCK, -(-min_size // BLOCK) * BLOCK)
    if disk is not False:
        size = max(size, DISK_BYTES)
        if SS_MAX < size < HD_MIN:
            size = HD_MIN               # keep the SDC from seeing it double sided
    blocks = size // BLOCK
    if blocks > MAX_BLOCKS:
        raise PackError("image larger than 24-bit block range")

    img = bytearray([pad_byte]) * size
    for a in assets:
        p = a.block * BLOCK + a.off
        img[p:p + len(a.data)] = a.data

    # build_id: CRC of the TOC entries, which hold each asset's place, size,
    # destination and CRC, so it changes whenever any of them does (and not
    # with the loader that embeds it)
    toc = bytearray()
    for a in assets:
        toc += a.descriptor() + struct.pack(">I", zlib.crc32(a.data))
    build_id = zlib.crc32(bytes(toc))
    hdr = (MAGIC + bytes([VERSION, 0 if disk is False else H_DISK])
           + struct.pack(">H", len(assets))
           + struct.pack(">I", build_id) + blocks.to_bytes(3, "big") + bytes([toc_blocks])
           + name.encode("ascii", "replace")[:16].ljust(16, b" "))
    assert len(hdr) == HDR_SIZE
    img[0:toc_bytes] = hdr + toc
    img[toc_bytes:data0] = bytes(data0 - toc_bytes)

    disk_files = []
    if disk is not False:
        used = [(0, data0)] + [(a.block * BLOCK + a.off, len(a.data)) for a in assets]
        files = []
        if loader:
            files.append(loader)
        if autoexec and loader:
            run = loader[0] + ("." + loader[1] if loader[1] else "")
            files.append(("AUTOEXEC", "BAS", f'10 LOADM"{run}":EXEC\r'.encode()))
        disk_files = write_disk(img, used, files)

    # ---- write outputs
    out_image = out(image)
    with open(out_image, "wb") as f:
        f.write(img)

    P = prefix
    where = f"mount 'M:{mount}'" if mount else "DECB disk, already mounted"
    eq = [f"; Generated by dmpkg from {os.path.basename(manifest)} - do not edit", "",
          f"; Image {os.path.basename(image)}  {where}",
          f"{P}SDC_BUILD_ID      equ     ${build_id:08X}",
          f"{P}SDC_IMAGE_BLOCKS  equ     ${blocks:06X}",
          f"{P}SDC_ASSET_COUNT   equ     {len(assets)}",
          f"{P}SDC_DESC_SIZE     equ     12", ""]
    for i, a in enumerate(assets):
        n = P + a.name
        eq += [f"; {a.name}: {a.src} ({a.kind}{', aligned' if a.align else ''})",
               f"{n+'_IDX':<18}equ     {i}",
               f"{n+'_BLK':<18}equ     ${a.block:06X}",
               f"{n+'_OFF':<18}equ     ${a.off:04X}",
               f"{n+'_LEN':<18}equ     ${len(a.data):06X}"]
        if a.kind == "decb":
            eq.append(f"{n+'_EXEC':<18}equ     ${a.exec_addr:04X}")
        else:
            eq.append(f"{n+'_DEST':<18}equ     ${a.dest:06X}")
        eq.append("")

    tb = [f"; Generated by dmpkg from {os.path.basename(manifest)} - do not edit", "",
          "; Assemble alongside the equates file. Descriptors are 12 bytes:",
          ";   +0 block(3) +3 offset(2) +5 length(3) +8 flags(1) +9 dest(3)",
          ";   flags: $80 phys (CoCo 3 MMU load)  $40 decb records",
          *([f"{P}SDC_MountName", f"        fcn     'M:{mount}'"] if mount else
            [f"{P}SDC_MountName   equ     0       ; no mount: the disk is already in SDC_DRIVE"]),
          "", f"{P}SDC_BuildID     ; header build_id (bytes 8-11), checked by sdc_open"]
    tb.append(f"        fqb     ${build_id:08X}")
    tb += ["", "; asset descriptors, in manifest order: ldx #NAME / jsr sdc_load"]
    lw = max(8, max(len(P + a.name) for a in assets) + 1)   # opcode column: 8, or past the longest name
    for a in assets:
        tb.append(f"{P + a.name:<{lw}}fcb     " + ",".join(f"${b:02X}" for b in a.descriptor()))

    for key, lines in (("equates", eq), ("table", tb)):
        p = proj.get(key)
        if p:
            with open(out(p), "w", encoding="ascii", newline="\n") as f:
                f.write("\n".join(lines).rstrip("\n") + "\n")

    report = [f"dmpkg: {name}  ->  {image}",
              f"  mount   {'M:' + mount if mount else 'none (DECB disk, already mounted)'}",
              f"  size    {size} bytes = {blocks} blocks ({end} used, TOC {toc_blocks} block(s))",
              f"  build   ${build_id:08X}"]
    if disk is not False:
        free = GRANULES - sum(n for _, n in disk_files)
        report.append("  disk    " + ", ".join(f"{f} {n}" for f, n in disk_files)
                      + f" granule(s), {free} free")
    report.append("")
    head = ("idx", "name", "block", "off", "length", "dest", "addr", "type")
    right = (True, False, True, True, True, False, True, False)   # numbers right-aligned
    dests = []
    for a in assets:
        if a.kind == "decb":
            dests.append(("exec", f"${a.exec_addr:04X}"))
        elif a.phys:
            dests.append(("phys", f"${a.dest:06X}"))
        else:
            dests.append(("cpu", f"${a.dest:04X}"))
    rows = []
    for i, (a, (dk, addr)) in enumerate(zip(assets, dests)):
        rows.append((str(i), a.name, str(a.block), str(a.off), str(len(a.data)),
                     dk, addr, a.kind + (" align" if a.align else "")))
    width = [max(len(r[c]) for r in rows + [head]) for c in range(len(head))]
    for r in [head] + rows:
        cells = [v.rjust(w) if rj else v.ljust(w) for v, w, rj in zip(r, width, right)]
        report.append(("  " + "  ".join(cells)).rstrip())
    if proj.get("map"):
        with open(out(proj["map"]), "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
    if verbose:
        print("\n".join(report))
    return {"image": out_image, "assets": assets, "build_id": build_id, "blocks": blocks,
            "mount": mount, "header": hdr, "disk_files": disk_files}


# ---------------------------------------------------------------- inspect

def read_image(path: str):
    with open(path, "rb") as f:
        img = f.read()
    if len(img) < HDR_SIZE or img[:4] != MAGIC:
        raise PackError(f"{path}: not an dmpkg image")
    ver, flags, count, bid = img[4], img[5], *struct.unpack_from(">HI", img, 6)
    blocks = int.from_bytes(img[12:15], "big")
    tocb = img[15]
    name = img[16:32].decode("ascii", "replace").rstrip()
    ents = []
    for i in range(count):
        e = img[HDR_SIZE + i * TOC_ENTRY: HDR_SIZE + (i + 1) * TOC_ENTRY]
        ents.append(dict(block=int.from_bytes(e[0:3], "big"), off=int.from_bytes(e[3:5], "big"),
                         length=int.from_bytes(e[5:8], "big"), flags=e[8],
                         dest=int.from_bytes(e[9:12], "big"), crc=struct.unpack(">I", e[12:16])[0]))
    return img, dict(version=ver, flags=flags, count=count, build_id=bid, blocks=blocks,
                     toc_blocks=tocb, name=name), ents


def read_disk(img: bytes):
    """DECB directory of a disk image: [(file, first granule, granule chain)].
    Raises PackError on a broken chain."""
    fat = img[DIR_LO + SECTOR:DIR_LO + SECTOR + GRANULES]
    files = []
    for d in range(DIR_LO + 2 * SECTOR, DIR_LO + 11 * SECTOR, 32):
        e = img[d:d + 32]
        if e[0] == 0xFF:
            break                                   # never used: end of directory
        if e[0] == 0x00:
            continue                                # killed
        name = e[0:8].decode("ascii", "replace").rstrip()
        ext = e[8:11].decode("ascii", "replace").rstrip()
        grans, g = [], e[13]
        while True:
            if g >= GRANULES or g in grans:
                raise PackError(f"{name}.{ext}: broken granule chain")
            grans.append(g)
            if fat[g] >= 0xC0:
                break
            g = fat[g]
        files.append((name + ("." + ext if ext else ""), grans))
    return files
