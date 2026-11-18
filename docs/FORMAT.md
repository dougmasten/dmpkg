# dmpkg image format (`.SDS` / `.DSK`), version 1

This is the on-card layout written by `dmpkg build` and read by `asm/dmpkg.asm` through the CoCo SDC `STREAM` command. All multi-byte integers are **big-endian** (6809 order). A *block* is 512 bytes, and block numbers are 24-bit, the same as the SDC's `$FF49–$FF4B` LSN registers.

An image comes in two forms with the same header, TOC and asset encoding:

- **Stream image** (`.SDS`): just the dmpkg data. The program mounts it by name.
- **Disk image** (`.DSK`, with `disk:` in the manifest): also a 35-track DECB disk. The dmpkg data sits in granules owned by a `DMPKG.DAT` file (or the name in `disk.data`), next to the loader program, so one file holds both. See [DECB disk image](#decb-disk-image).

## Overall layout

```
block 0 ..          header (32 bytes) + TOC (16 bytes × count), zero-padded
                    to a whole number of blocks  → toc_blocks blocks
block toc_blocks .. asset data, placed first-fit in manifest order
...                 pad_byte fill up to a whole block and to min_size
```

| region | size |
|---|---|
| header | 32 bytes at offset 0 |
| TOC | `count` × 16 bytes, right after the header |
| TOC padding | `$00` up to `toc_blocks × 512` |
| asset data | starts at block `toc_blocks` |
| tail padding | `pad_byte` (default `$00`, `$FF` on a disk image) |

**File size** = `blocks × 512`, rounded up to at least `min_size` (default and minimum `$14400` = 82,944 bytes = 162 blocks). The runtime mounts the image with `M:`, which only accepts a headerless file as a disk image if it's at least 324 sectors (82,944 bytes), according to the SDC User Guide. STREAM itself has no size limit.

## Header (32 bytes, offset 0)

| off | size | field | value |
|---|---|---|---|
| 0 | 4 | magic | ASCII `DMPK` (`$44 $4D $50 $4B`) |
| 4 | 1 | version | `$01` |
| 5 | 1 | flags | `$01` disk image (also a DECB disk), else `$00`; other bits reserved |
| 6 | 2 | count | number of assets / TOC entries |
| 8 | 4 | build_id | CRC-32, see below |
| 12 | 3 | blocks | total image size in blocks |
| 15 | 1 | toc_blocks | blocks used by header + TOC (1–255); first data block |
| 16 | 16 | name | project name, ASCII, space-padded, not terminated |

`sdc_open` compares `build_id` (bytes 8–11) against `DMPKG_BuildID` from the generated `assettab.asm`, so a program built against a different image fails with error 4. The other header fields aren't checked: the TOC, and through its CRCs the data, are already covered by `build_id`.

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

- **Placement:** first fit, in manifest order. Each asset takes the first free space it fits in, at any offset, so assets normally follow each other back to back. An asset marked `align: true` starts on a fresh block (`offset = 0`) instead. The gap that leaves stays free, and a later asset small enough to fit goes there, so image order can differ from manifest order. Unused bytes are `pad_byte`. The first asset always starts at the first data block, offset 0. On a disk image no asset crosses track 17 (see below).
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
- **build_id:** the CRC of the TOC entries (`count × 16` bytes, not the header and not the TOC padding). Each entry holds its asset's place, length, flags, destination and CRC, so any change to an asset changes the ID, while padding and the DECB file system (including the loader, which embeds the ID) don't. The same inputs always give the same ID, and a project gives the same ID as a stream image or a disk image when the layout is the same.

`dmpkg verify` checks the file size against `blocks` and `min_size`, recomputes every entry CRC, and recomputes `build_id`. On a disk image it also checks the size against the SDC's geometry rules, that no asset touches track 17, that the granule chains are sound, and that every granule holding header, TOC or asset bytes belongs to the data file (`DMPKG.DAT`, or whichever file owns granule 0, where the header is) and to no other file.

## Mount name (not stored in the image)

The runtime mounts the image with the string in `DMPKG_MountName`: `M:` followed by the `sdc_path`, upper-cased and NUL-terminated. Each part of the path must be an 8.3 name, separated by `/`. Example: `games/demo.sds` → `M:GAMES/DEMO.SDS`.

A disk image with no `sdc_path` has no mount name: `DMPKG_MountName` is an `equ 0`, and `sdc_open` with X = 0 streams whatever is already mounted in `SDC_DRIVE`, which is the disk the loader was run from.

## DECB disk image

A disk image is a standard single-sided DECB disk: 35 tracks of 18 sectors of 256 bytes, laid out in the file track by track (byte offset = (track × 18 + sector − 1) × 256). Track 17 holds the file system:

| sector | contents |
|---|---|
| 2 | FAT: 68 bytes, one per granule (`$FF` free, `$00–$43` next granule, `$C1–$C9` last granule with 1–9 sectors used), then `$00` |
| 3–11 | directory: 32-byte entries, `$FF` = never used |
| others | `$FF` |

The other 34 tracks are 68 granules of 9 sectors (2,304 bytes), two per track, numbered upward and skipping track 17: granule *g* starts at track `g/2` (plus one from track 17 on), sector `1 + 9·(g mod 2)`. An odd granule starts in the middle of a 512-byte block, which the descriptors' byte offset already handles.

- **dmpkg data:** the header and TOC stay at block 0, inside granule 0. Every granule that holds header, TOC or asset bytes is chained, in ascending order, into one file, `DMPKG.DAT` by default or the name in `disk.data` (type 1 data, binary). Disk BASIC then counts those granules as used and never hands them to another file. The runtime ignores the FAT and reads by block and offset as usual.
- **Track 17:** a stream runs straight through the file, so an asset that crossed track 17 would read directory sectors as data. Assets are placed in tracks 0–16 (blocks 1–152, up to byte 78,336) and from track 18 on (block 162, byte 82,944); first fit fills the space left before track 17 with later assets that fit.
- **Past track 34:** assets that don't fit carry on past the disk area (block 315, byte 161,280). Disk BASIC never sees that part.
- **Files:** the loader (`disk.loader`, a LOADM file, type 2 binary) and the optional `AUTOEXEC.BAS` (type 0, ASCII: `10 LOADM"LOADER.BIN":EXEC`) go into the granules left after the assets, lowest first. The directory lists `DMPKG.DAT`, the loader, then `AUTOEXEC.BAS`.
- **Size:** at least 161,280 bytes (630 sectors). The SDC picks a disk's geometry from its size: 721–2880 sectors means double-sided, which would move track 17. An image that grows past 720 sectors (184,320 bytes) is padded to 2882 sectors (737,792 bytes, the first whole number of blocks over 2880), which the SDC treats as a hard disk image, seen as single-sided by floppy emulation.

## Worked example (`example/build/DEMO.DSK`)

```
00000000: 44 4d 50 4b 01 01 00 06 6a 82 1b d0 00 01 3b 01  DMPK....j.....;.
00000010: 44 65 6d 6f 20 20 20 20 20 20 20 20 20 20 20 20  Demo
00000020: 00 00 01 00 00 00 02 ff 00 00 60 00 1d a2 53 d6  FONT    blk 1 off 0 len 767 cpu $6000
00000030: 00 00 02 00 ff 00 00 10 00 00 64 00 73 1d bb 74  PALETTE blk 2 off 255 (packed after FONT)
00000060: 00 00 0a 00 00 00 4e 21 80 06 00 01 9f b5 25 a3  TITLE   blk 10 off 0 (align: true), flags $80 phys $060001
00000070: 00 00 09 00 6b 00 01 86 40 00 00 00 c6 fb 89 72  CODE    blk 9 off 107 (in TITLE's align gap), flags $40 decb
```

Version 1, disk image (flags `$01`), 6 assets, build `$6A821BD0`, 315 blocks (`$13B`, 35 tracks), 1 TOC block. The assets fill granules 0–10 (`DMPKG.DAT`), `LOADER.BIN` is granule 11 and `AUTOEXEC.BAS` granule 12.
