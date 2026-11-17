# dmpkg stream image format (`.SDS`), version 1

This is the on-card layout written by `dmpkg build` and read by `asm/dmpkg.asm` through the CoCo SDC `STREAM` command. All multi-byte integers are **big-endian** (6809 order). A *block* is 512 bytes, and block numbers are 24-bit, the same as the SDC's `$FF49–$FF4B` LSN registers.

## Overall layout

```
block 0 ..          header (32 bytes) + TOC (16 bytes × count), zero-padded
                    to a whole number of blocks  → toc_blocks blocks
block toc_blocks .. asset data, in manifest order
...                 pad_byte fill up to a whole block and to min_size
```

| region | size |
|---|---|
| header | 32 bytes at offset 0 |
| TOC | `count` × 16 bytes, right after the header |
| TOC padding | `$00` up to `toc_blocks × 512` |
| asset data | starts at block `toc_blocks` |
| tail padding | `pad_byte` (default `$00`) |

**File size** = `blocks × 512`, rounded up to at least `min_size` (default and minimum `$14400` = 82,944 bytes = 162 blocks). The runtime mounts the image with `M:`, which only accepts a headerless file as a disk image if it's at least 324 sectors (82,944 bytes), according to the SDC User Guide. STREAM itself has no size limit.

## Header (32 bytes, offset 0)

| off | size | field | value |
|---|---|---|---|
| 0 | 4 | magic | ASCII `DMPK` (`$44 $4D $50 $4B`) |
| 4 | 1 | version | `$01` |
| 5 | 1 | flags | `$00` (reserved) |
| 6 | 2 | count | number of assets / TOC entries |
| 8 | 4 | build_id | CRC-32, see below |
| 12 | 3 | blocks | total image size in blocks |
| 15 | 1 | toc_blocks | blocks used by header + TOC (1–255); first data block |
| 16 | 16 | name | project name, ASCII, space-padded, not terminated |

`sdc_open` compares `build_id` (bytes 8–11) against `SDC_BuildID` from the generated `assettab.asm`, so a program built against a different image fails with error 2. The other header fields aren't checked: the TOC and data are already covered by `build_id`.

## TOC entry (16 bytes)

Entry *i* sits at offset `32 + 16·i`. Its first 12 bytes are the **asset descriptor**. The same 12 bytes are emitted as the `NAME` label in `assettab.asm` and passed in X to `sdc_load`.

| off | size | field | meaning |
|---|---|---|---|
| 0 | 3 | block | block where the asset's data starts |
| 3 | 2 | offset | byte offset inside that block (0–511; always 0 for aligned assets) |
| 5 | 3 | length | stored length in bytes (after DECB re-encoding) |
| 8 | 1 | flags | see below |
| 9 | 3 | dest | load address; meaning depends on flags |
| 12 | 4 | crc32 | CRC-32 of the stored bytes (not part of the descriptor) |

Absolute file offset of the data = `block × 512 + offset`.

### Flags

| bit | mask | name | meaning |
|---|---|---|---|
| 7 | `$80` | PHYS | `dest` is a CoCo 3 physical address (`$000000–$1FFFFF`), loaded through the MMU window |
| 6 | `$40` | DECB | data is a DECB record stream (below); `dest` is `0` |
| 5–0 | | | reserved, `0` |

With neither PHYS nor DECB set, `dest` is a 16-bit CPU address held in 3 bytes (top byte `$00`). `dest + length` never goes past `$10000`.

## Asset data

- **Alignment:** assets are packed: each starts on the byte right after the previous asset ends, at any offset. An asset marked `align: true` starts on a fresh block (`offset = 0`) instead, and the gap before it is filled with `pad_byte`. The first asset always starts at the first data block, offset 0.
- **Raw:** the file's bytes, unchanged.
- **DECB:** a LOADM binary is re-encoded as a record stream that `sdc_load` follows:

  ```
  repeat: len(2) addr(2) data(len)      one per non-empty segment
  end:    $0000  exec(2)                 sdc_load returns exec in U
  ```

  Unlike the original LOADM file, records have no `$00`/`$FF` type byte. A zero length marks the end. Empty segments are dropped. Stored `length` covers the whole record stream, terminator included.

## Checksums

All CRCs are standard CRC-32 (IEEE 802.3, reflected, init/xorout `$FFFFFFFF`; Python `zlib.crc32`).

- **Entry crc32:** the CRC of the `length` stored bytes at the asset's offset.
- **build_id:** the CRC of the TOC entries (`count × 16` bytes, not the header and not the TOC padding) followed by every byte from block `toc_blocks` to the end of the file, padding included. The same inputs always give the same ID.

`dmpkg verify` checks the file size against `blocks` and `min_size`, recomputes every entry CRC, and recomputes `build_id`.

## Mount name (not stored in the image)

The runtime mounts the image with the string in `SDC_MountName`: `M:` followed by the `sdc_path`, upper-cased and NUL-terminated. Each part of the path must be an 8.3 name, separated by `/`. Example: `games/demo.sds` → `M:GAMES/DEMO.SDS`.

## Worked example (`example/build/DEMO.SDS`)

```
00000000: 44 4d 50 4b 01 00 00 06 d1 d5 86 ce 00 00 a2 01  DMPK............
00000010: 44 65 6d 6f 20 20 20 20 20 20 20 20 20 20 20 20  Demo
00000020: 00 00 01 00 00 00 02 ff 00 00 60 00 1d a2 53 d6  FONT    blk 1 off 0 len 767 cpu $6000
00000030: 00 00 02 00 ff 00 00 10 00 00 64 00 73 1d bb 74  PALETTE blk 2 off 255 (packed after FONT)
00000060: 00 00 0a 00 00 00 4e 21 80 07 a0 01 9f b5 25 a3  TITLE   blk 10 off 0 (align: true), flags $80 phys $07A001
00000070: 00 00 31 00 21 00 01 86 40 00 00 00 c6 fb 89 72  CODE    blk 49 off 33, flags $40 decb
```

Version 1, 6 assets, build `$D1D586CE`, 162 blocks (`$A2`), 1 TOC block.
