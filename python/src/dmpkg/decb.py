"""
Disk Extended Color BASIC: LOADM files (parsing them and re-encoding them as
the records dmpkg_load follows), 8.3 file names, and the 35-track disk a disk
image doubles as (writing its FAT and directory, and reading them back).
"""
from __future__ import annotations

import os
import re
import struct

from . import basic
from .errors import PackError

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

NAME_CHARS = r"[A-Z0-9_$#!&@%^()'{}~-]"


# ---------------------------------------------------------------- LOADM files

def parse_loadm(raw: bytes, src: str):
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


def records(segs, exe) -> bytes:
    """Stored form: [len.w addr.w data]... [0000 exec.w] - read by dmpkg_load."""
    out = bytearray()
    for addr, data in segs:
        if len(data) > 0xFFFF:
            raise PackError("DECB segment over 64K")
        out += struct.pack(">HH", len(data), addr) + data
    out += struct.pack(">HH", 0, exe)
    return bytes(out)


def loadm(stored: bytes, src: str) -> bytes:
    """The stored records back as a LOADM file: a $00 preamble per segment,
    then the $FF postamble with the exec address."""
    out, i = bytearray(), 0
    while True:
        if i + 4 > len(stored):
            raise PackError(f"{src}: DECB records end without an exec record")
        ln, addr = struct.unpack_from(">HH", stored, i)
        if ln == 0:
            return bytes(out + b"\xff\x00\x00" + struct.pack(">H", addr))
        data = stored[i + 4:i + 4 + ln]
        if len(data) != ln:
            raise PackError(f"{src}: truncated DECB record at {i}")
        out += b"\x00" + struct.pack(">HH", ln, addr) + data
        i += 4 + ln


def file_name(path: str, what: str) -> tuple[str, str]:
    """'build/loader.bin' -> ('LOADER', 'BIN'), checked as a DECB 8.3 name."""
    leaf = os.path.basename(path).upper()
    base, _, ext = leaf.rpartition(".")
    if not base:
        base, ext = ext, ""
    if not re.fullmatch(NAME_CHARS + "{1,8}", base) or len(ext) > 3 \
            or not re.fullmatch(NAME_CHARS + "*", ext):
        raise PackError(f"{what}: '{leaf}' is not a valid DECB 8.3 name")
    return base, ext


def autoexec(name: str, ext: str, tokenize: bool = True) -> tuple[str, str, bytes]:
    """AUTOEXEC.BAS that runs the LOADM file name.ext: a tokenized program,
    or with tokenize=False the same line as ASCII (Disk BASIC tokenizes it
    when it loads it)."""
    run = name + ("." + ext if ext else "")
    line = f'10 LOADM"{run}":EXEC\r'
    return "AUTOEXEC", "BAS", basic.tokenize(line) if tokenize else line.encode()


# ---------------------------------------------------------------- the disk

def gran_offset(g: int) -> int:
    """Byte offset of granule g in the disk image (granules skip track 17)."""
    t = g // 2
    t += t >= DIR_TRACK
    return t * TRACK_BYTES + (g % 2) * GRAN_BYTES


def gran_of(pos: int) -> int:
    """Granule holding byte pos (pos must be in the disk area, not track 17)."""
    t = pos // TRACK_BYTES
    return (t - (t > DIR_TRACK)) * 2 + (pos % TRACK_BYTES) // GRAN_BYTES


def write_disk(img: bytearray, used: list, files: list, data=("DMPKG", "DAT")) -> list:
    """Make the first 35 tracks of img a DECB disk. used holds (pos, length)
    runs of dmpkg data (header, TOC, assets); every granule they touch goes
    to the data file (DMPKG.DAT unless data names another), so Disk BASIC
    never hands it out. files holds (name, ext,
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
    entries = [(*data, 1, False, dat[0], chain(dat, reach[dat[-1]]))]
    out = [(data[0] + ("." + data[1] if data[1] else ""), len(dat))]
    for name, ext, data in files:
        free = [g for g in range(GRANULES) if fat[g] == 0xFF]
        need = -(-len(data) // GRAN_BYTES)
        if need > len(free):
            raise PackError(f"disk full: {name}.{ext} needs {need} granule(s), "
                            f"{len(free)} left after the assets (project.disk.reserve "
                            "keeps granules free for the loader and AUTOEXEC.BAS)")
        grans = free[:need]
        for i, g in enumerate(grans):
            part = data[i * GRAN_BYTES:(i + 1) * GRAN_BYTES]
            img[gran_offset(g):gran_offset(g) + len(part)] = part
        prog = ext == "BAS"                     # BASIC program, else LOADM file;
        ascii_ = prog and data[:1] != b"\xff"   # a tokenized one starts with $FF
        entries.append((name, ext, 0 if prog else 2, ascii_, grans[0],
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


def read_disk(img: bytes):
    """DECB directory of a disk image: [(file, granule chain)].
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


def read_files(img: bytes, src: str = "disk") -> list[dict]:
    """Every file on a DECB disk image, in directory order, as
    dict(name, type, ascii, data, grans, last): type is the directory's file
    type (0 BASIC, 1 data, 2 machine language, 3 text), ascii its ASCII flag,
    grans the granule chain and last the bytes used in the last sector.
    Raises PackError on a disk that's too small or a broken granule chain."""
    if len(img) < DISK_BYTES:
        raise PackError(f"{src}: {len(img)} bytes, under a 35-track disk ({DISK_BYTES})")
    fat = img[DIR_LO + SECTOR:DIR_LO + SECTOR + GRANULES]
    out = []
    for d in range(DIR_LO + 2 * SECTOR, DIR_LO + 11 * SECTOR, 32):
        e = img[d:d + 32]
        if e[0] == 0xFF:
            break                                   # never used: end of directory
        if e[0] == 0x00:
            continue                                # killed
        name = e[0:8].decode("ascii", "replace").rstrip()
        ext = e[8:11].decode("ascii", "replace").rstrip()
        full = name + ("." + ext if ext else "")
        last = e[14] << 8 | e[15]                   # bytes used in the last sector
        data, g, seen, seen_order = bytearray(), e[13], set(), []
        while True:
            if g >= GRANULES or g in seen:
                raise PackError(f"{src}: {full}: broken granule chain")
            seen.add(g)
            seen_order.append(g)
            nxt, at = fat[g], gran_offset(g)
            if nxt >= 0xC0:                         # last granule: low bits = sectors used
                n = nxt & 0x1F
                if not 1 <= n <= 9 or last > SECTOR:
                    raise PackError(f"{src}: {full}: bad last granule (FAT ${nxt:02X}, "
                                    f"{last} bytes in its last sector)")
                data += img[at:at + (n - 1) * SECTOR + last]
                break
            data += img[at:at + GRAN_BYTES]
            g = nxt
        out.append(dict(name=full, type=e[11], ascii=e[12] == 0xFF, data=bytes(data),
                        grans=[*seen_order], last=last))
    return out
