"""
The dmpkg image format (docs/FORMAT.md): the 32-byte header and the TOC in
block 0, the 12-byte asset descriptor, the build_id, and reading an image
back.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from .errors import PackError

BLOCK = 512
MIN_IMAGE = 0x14400          # smallest DSK that M: mounts, per the SDC User Guide
                             # (324 sectors = 82,944 bytes = 162 blocks)
MAX_BLOCKS = 1 << 24         # 24-bit block number
MAGIC = b"DMPK"
FORMAT = 1                   # header byte 8: bump on any change to the header or TOC layout
HDR_SIZE = 32
TOC_ENTRY = 16

F_PHYS = 0x80                # dest is a CoCo 3 physical address (MMU-windowed load)
F_DECB = 0x40                # stored as segment records, loader follows them

H_DISK = 0x01                # header flags: the image is also a DECB disk


@dataclass
class Asset:
    name: str                 # symbol (upper-case)
    src: str                  # source file, as the manifest gives it
    kind: str                 # raw | decb
    dest: int = 0
    phys: bool = False
    align: bool = False       # start on a fresh 512-byte block
    data: bytes = b""         # stored bytes (decb: the record stream)
    exec_addr: int | None = None
    segs: list = field(default_factory=list)
    block: int = 0            # placement, set by layout.place()
    off: int = 0

    @property
    def pos(self) -> int:
        """Byte offset of the asset's data in the image."""
        return self.block * BLOCK + self.off

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


def toc_size(count: int) -> tuple[int, int]:
    """(bytes, blocks) taken by the header and a TOC of count entries."""
    size = HDR_SIZE + TOC_ENTRY * count
    blocks = -(-size // BLOCK)
    if blocks > 255:
        raise PackError("too many assets for the TOC")
    return size, blocks


def toc(assets: list[Asset]) -> bytes:
    """TOC entries: each asset's descriptor and the CRC-32 of its data."""
    return b"".join(a.descriptor() + struct.pack(">I", zlib.crc32(a.data)) for a in assets)


def build_id(toc_entries: bytes) -> int:
    """CRC-32 of the TOC entries. They hold every asset's place, size,
    destination and CRC, so it changes whenever any of them does, and not
    with padding or the DECB file system (the loader embeds it)."""
    return zlib.crc32(toc_entries)


def header(title: str, count: int, build_id: int, blocks: int, disk: bool) -> bytes:
    hdr = (MAGIC + struct.pack(">I", build_id) + bytes([FORMAT])
           + struct.pack(">H", count) + blocks.to_bytes(3, "big")
           + bytes([H_DISK if disk else 0, 0])                              # byte 15 reserved
           + title.encode("ascii", "replace")[:16].ljust(16, b" "))
    assert len(hdr) == HDR_SIZE
    return hdr


def read_image(path: str):
    """Read an image: (bytes, header fields, TOC entries)."""
    with open(path, "rb") as f:
        img = f.read()
    if len(img) < HDR_SIZE or img[:4] != MAGIC:
        raise PackError(f"{path}: not a dmpkg image")
    bid, fmt, count = struct.unpack_from(">IBH", img, 4)
    if fmt != FORMAT:
        raise PackError(f"{path}: dmpkg format {fmt}, but this dmpkg reads format {FORMAT}")
    blocks = int.from_bytes(img[11:14], "big")
    flags = img[14]
    title = img[16:32].decode("ascii", "replace").rstrip()
    ents = []
    for i in range(count):
        e = img[HDR_SIZE + i * TOC_ENTRY: HDR_SIZE + (i + 1) * TOC_ENTRY]
        ents.append(dict(block=int.from_bytes(e[0:3], "big"), off=int.from_bytes(e[3:5], "big"),
                         length=int.from_bytes(e[5:8], "big"), flags=e[8],
                         dest=int.from_bytes(e[9:12], "big"), crc=struct.unpack(">I", e[12:16])[0]))
    return img, dict(format=fmt, flags=flags, count=count, build_id=bid, blocks=blocks,
                     title=title), ents
