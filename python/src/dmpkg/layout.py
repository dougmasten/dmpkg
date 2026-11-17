"""
Where things go in an image: assets placed first fit in manifest order
(never across a disk's track 17), and the image's final size.
"""
from __future__ import annotations

from .decb import DIR_HI, DIR_LO, DISK_BYTES, HD_MIN, SS_MAX
from .errors import PackError
from .image import BLOCK, MAX_BLOCKS, Asset


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


def place(assets: list[Asset], data0: int, disk: bool) -> int:
    """Set each asset's block and offset; return the end of the last byte.

    Data starts at data0 (after the header and TOC). Each asset, in manifest
    order, takes the first free space it fits, so a small asset can fill an
    align gap (or, on a disk, the space before track 17) left earlier. A
    stream can't jump track 17, so on a disk no asset crosses it."""
    if not disk:
        free = [(data0, None)]
    else:
        if data0 > DIR_LO:
            raise PackError("TOC runs into the DECB directory track")
        free = [(data0, DIR_LO), (DIR_HI, None)]
    for a in assets:
        a.block, a.off = divmod(first_fit(free, len(a.data), a.align), BLOCK)
    return max(a.pos + len(a.data) for a in assets)


def image_size(end: int, min_size: int, disk: bool) -> int:
    """Image size in bytes: whole blocks, at least min_size; a disk is at
    least 35 tracks and kept out of the SDC's double-sided size range."""
    size = max(-(-end // BLOCK) * BLOCK, -(-min_size // BLOCK) * BLOCK)
    if disk:
        size = max(size, DISK_BYTES)
        if SS_MAX < size < HD_MIN:
            size = HD_MIN               # keep the SDC from seeing it double sided
    if size // BLOCK > MAX_BLOCKS:
        raise PackError("image larger than 24-bit block range")
    return size
