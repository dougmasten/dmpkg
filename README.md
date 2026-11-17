# dmpkg — project disc packer for the CoCo SDC STREAM protocol

`dmpkg` packs all of a project's assets into a single **stream image** on the SD card. A small 6809 runtime (`asm/dmpkg.asm`, 966 bytes) then loads any asset straight to its destination using the CoCo SDC `STREAM` command. That includes CoCo 3 physical RAM through an MMU window.

```
project.yaml ──► dmpkg build ───► build/DEMO.SDS      → SD card
                                  ├─► build/assets.inc     EQUs (no bytes)
                                  ├─► build/assettab.asm   mount name, build_id, descriptors
                                  └─► build/DEMO.map       layout report
```

## Quick start

```sh
pip install -e python              # dmpkg command (or: PYTHONPATH=python/src python3 -m dmpkg)
python3 example/mkassets.py         # writes example/assets/ (random filler, fixed seed)
dmpkg build example/project.yaml -v
dmpkg verify example/build/DEMO.SDS
dmpkg list   example/build/DEMO.SDS
```

```asm
        ldx     #SDC_MountName      from assettab.asm
        ldy     #SDC_BuildID        0 to skip the stale-image check
        jsr     sdc_open
        bcs     error               A = error code
        ldx     #TITLE              asset descriptor
        jsr     sdc_load
        bcs     error
        ldx     #CODE               decb asset
        jsr     sdc_load            U = exec address
        jsr     sdc_close           back to floppy emulation
        ...
        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
```

Requires Python 3.8+ and PyYAML (installed by `pip install -e python`).

```
python/         the packer, a Python project of its own
  pyproject.toml
  src/dmpkg/    pack.py (build, image reading), cli.py (commands)
  tests/        end-to-end test of the packer and runtime (run_test.py)
asm/            the 6809 runtime (dmpkg.asm)
example/        example project and asset generator
docs/           image format
Makefile        builds the example and runs the tests across both parts
```

## Manifest (YAML)

```yaml
project:
  name: DEMO                       # up to 16 chars, stored in the header
  image: build/DEMO.SDS            # output image (required)
  sdc_path: GAMES/DEMO.SDS         # where it lives on the card; 8.3, dirs ≤ 8 chars
  equates: build/assets.inc        # optional outputs
  table: build/assettab.asm
  map: build/DEMO.map
  prefix: ""                       # prepended to every generated symbol
  min_size: 0x14400                # default and minimum; the SDC won't mount smaller
  pad_byte: 0

assets:
  - name: font                     # becomes FONT / FONT_BLK / FONT_LEN ...
    file: assets/font.bin
    load: "$6000"                  # CPU address, must fit below $10000
  - name: palette
    file: assets/palette.bin
    load: 0x6400                   # packed right after the previous asset (default)
  - name: title
    file: assets/title.gfx
    phys: 0x7A001                  # CoCo 3 physical address (2MB range)
    align: true                    # start on a fresh 512-byte block
  - name: map
    file: assets/map.bin
    load: 0x7000
  - name: code
    file: assets/code.bin
    type: decb                     # LOADM file; each segment goes to its own address
```

- **Numbers:** a YAML int (`0x6000`, `24576`) or a string (`"$6000"`, `"&H6000"`, `"%1010"`). An unquoted `$6000` also works, because YAML reads it as text.
- **Layout:** first fit in manifest order. Each asset takes the first free space it fits in, so assets normally follow each other back to back. `align: true` starts an asset on a fresh 512-byte block instead, and a later asset small enough to fit goes in the gap that leaves (the example's CODE sits in the gap before TITLE). Assets you load in image order come in one stream with no restart. Out of order, `sdc_load` restarts at the asset's block and skips to its offset, which costs a STREAM command and a card seek, so `align` mostly matters if you want block-aligned data. The map report and `dmpkg list` show where everything went.
- **Validation:** unknown keys, overlapping `load`/`phys`, loads that run past `$FFFF` or 2MB, bad 8.3 names and malformed DECB files are all errors.

## Generated symbols

| symbol | meaning |
|---|---|
| `NAME` | 12-byte descriptor (pass in X to `sdc_load`) |
| `NAME_IDX` `NAME_BLK` `NAME_OFF` | index, start block, byte offset in block |
| `NAME_LEN` | stored length (for decb, the record stream) |
| `NAME_DEST` / `NAME_EXEC` | destination (CPU or physical), or DECB exec address |
| `SDC_MountName` `SDC_BuildID` | mount string, expected 4-byte build_id |
| `SDC_BUILD_ID` `SDC_IMAGE_BLOCKS` `SDC_ASSET_COUNT` | build info |

Descriptor: `+0 block(3) +3 offset(2) +5 length(3) +8 flags(1) +9 dest(3)`. Flags: `$80` phys, `$40` decb.

## Runtime

| call | in | out |
|---|---|---|
| `sdc_open` | X = mount name, or 0 for the image already in `SDC_DRIVE`; Y = build_id or 0 | firmware checked, stream parked after the build_id |
| `sdc_version` | — | D = firmware version in BCD (`$0124`), `0` if before 113; also in `sdc_ver` |
| `sdc_load` | X = descriptor | asset loaded; U = exec for decb |
| `sdc_getbyte` | — | A = next stream byte |
| `sdc_stop` | — | stream aborted (`$D0`), still in command mode |
| `sdc_close` | — | stopped, `$FF40` = 0 (floppy emulation back) |

On error, carry is set and A holds an error code (see [Error codes](#error-codes)). The stream is always stopped on error.

### Error codes

Every call returns carry clear on success. On failure it returns carry set and the code in A. The runtime defines the codes as `SDC_E*` equates, so compare against the names rather than the numbers.

| A | name | returned by | meaning | usual cause / fix |
|---|---|---|---|---|
| 1 | `SDC_EMOUNT` | `sdc_open` | the SDC couldn't mount the image (FAILED set after the name was sent) | the file isn't on the card at `sdc_path`, or the path or name is wrong. Check that the card has `GAMES/DEMO.SDS` (or your path) |
| 2 | `SDC_EHDR` | `sdc_open` | the image's `build_id` doesn't match `SDC_BuildID` | stale image: the card and the program come from different builds. Copy the current image to the card, or reassemble against it |
| 3 | `SDC_EEOF` | `sdc_open`, `sdc_load`, `sdc_getbyte` | BUSY dropped while waiting for READY: the stream reached end of file before the data | the image is truncated or isn't the one the descriptors were built for. Usually only seen with `Y = 0`, where the stale-image check is skipped |
| 4 | `SDC_ETIME` | any call | BUSY or READY didn't change within `SDC_TIMEOUT` × 65536 polls | no SDC answering (missing, or wrong Multi-Pak slot), or a stuck controller. Raise `SDC_TIMEOUT` only if a slow card really needs it |
| 5 | `SDC_EFAIL` | `sdc_open`, `sdc_load`, `sdc_getbyte` | the controller reported an error: FAILED (status bit 7) set, or the stream ended (BUSY cleared) with status `$08` instead of `$00`. The firmware ends a failed stream with `$08` and doesn't set FAILED, contrary to the User Guide | SD card read error, card removed, or the controller rejected the command |
| 6 | `SDC_EVER` | `sdc_open` | firmware is older than `SDC_MINVER` (117, the first with STREAM) | update the SDC firmware. `sdc_ver` holds the version it found (BCD), or 0 if the firmware predates the VERSION command (before 113) |

`sdc_version` itself only fails with `SDC_ETIME`: old firmware isn't an error there, it just returns 0. `sdc_stop` and `sdc_close` always succeed.

**Firmware check:** `sdc_open` first sends the SDC `VERSION` command (`$C0`, `$FF49` = `'V'`). The command arrived in firmware 113, and older firmware answers FAILED, which counts as version 0. `STREAM` arrived in 117. If the version is below `SDC_MINVER` (`$0117`), `sdc_open` returns error 6 before mounting anything. `sdc_ver` keeps the version it read, so you can report it. Define `SDC_NOVERCHK` to skip the check.

`sdc_load` skips the restart when the stream is already sitting at the asset. Otherwise it restarts at the asset's block and skips its offset. Copies run in chunks capped by three things: the end of the 512-byte block, the bytes left, and the end of the MMU window. Inside a chunk the inner loop copies 32 bytes per pass: 16 × `LDD $FF4A / STD n,U` with 5-bit offsets (U points at the middle of the group), then one `LEAU 32,U` and a `DEC`/`BNE` group counter, about 6 cycles per byte. The 0–31 byte tail goes 2 bytes at a time. Odd offsets and odd lengths take one byte from `$FF4B` or `$FF4A`, so the A-then-B read order is always kept.

**Phys loads:** the asset is mapped through the 8K window at `SDC_WINDOW` (default `$4000–$5FFF`). Its MMU register `SDC_MMUREG` is worked out from the window as the task 0 register `$FFA0 + SDC_WINDOW/$2000` (`$FFA2` by default). Define `SDC_MMUREG` yourself to use task 1. It must be an MMU register (`$FFA0–$FFAF`) for the same 8K slot as the window, so `$FFA2` or `$FFAA` for `$4000`. The window must be 8K-aligned and between `$2000` and `$C000`, or the include stops with an assembly error. The next 8K block is mapped every time the window fills. Afterwards the register is put back to the value it held before. If your board doesn't read MMU registers back cleanly, define `SDC_MMU_HOME`. Keep code, stack and the descriptor out of the window.

Configure these before the include: `SDC_DRIVE` (0), `SDC_WINDOW` ($4000), `SDC_MMUREG` (from `SDC_WINDOW`), `SDC_MMU_HOME`, `SDC_DELAY` (12), `SDC_TIMEOUT` (16), `SDC_MINVER` ($0117), `SDC_NOVERCHK`. The runtime is plain 6809 and assembles unchanged with `--6309`.

## Image format

Block 0 holds a 32-byte header, then 16-byte TOC entries. Asset data starts at the first block after the TOC.

```
header: "DMPK" ver(1) flags(1) count(2) build_id(4) blocks(3) toc_blocks(1) name(16)
entry:  descriptor(12) crc32(4)
```

`build_id` is a CRC-32 of the TOC entries. They hold each asset's place, size, destination and CRC, so the same content always gives the same ID and any change to an asset gives a new one. `sdc_open` compares the image's `build_id` with `SDC_BuildID` from `assettab.asm`, so a program built against a different image fails with error 2 instead of loading garbage. The image is padded to whole blocks and to at least `$14400` bytes (82,944). That's the smallest disk image the SDC's `M:` mount accepts according to the User Guide; STREAM itself has no limit. The full byte-level spec is in [docs/FORMAT.md](docs/FORMAT.md).

## Tests

`python/tests/run_test.py` assembles the real runtime with lwasm and runs it on a 6809 core (`pip install -e 'python[test]'` installs it, MC6809). It talks to a strict CoCo SDC model and a CoCo 3 MMU. The model raises an error if data is read before READY, if READY isn't given time to settle (it stays stale for 20 cycles after each block), if `$FF4B` is read before `$FF4A`, if a register is read within 20µs of a command, if a read passes end of file, if `M:` is given an image under 82,944 bytes, or if a STREAM is issued on firmware older than 117. It also covers the firmware gate: 117 is accepted, while 116 and pre-113 firmware (no VERSION command) both give error 6 and never mount. A further run splits a DECB record's length word across a block that fails, and checks that error 5 comes back in A, both for the firmware's real behaviour (the stream ends with status `$08`) and for the FAILED bit the User Guide describes. Another stalls the card part way through the phys load, so READY never comes: with the runtime assembled at `SDC_TIMEOUT=1` to keep it short, it checks for error 4, a restored MMU window and a stopped stream. The test checks every loaded byte against the sources.

The test doesn't use `example/assets/`. Each run writes fresh random assets of the same sizes to a temp folder, with a new seed, and builds `example/project.yaml` there. It prints the seed, and `TEST_SEED=n` repeats that run. `example/mkassets.py` makes the example's own assets with a fixed seed, so the example image and the worked example in [docs/FORMAT.md](docs/FORMAT.md) come out the same every time.

```sh
make test                       # build + verify the example, assemble the runtime, run the test
make test TEST_SEED=1234        # repeat a run with the seed it printed
make test LWASM=/path/to/lwasm  # if lwasm isn't on PATH
```

Other targets: `make build`, `make verify`, `make list`, `make asm` (assembles the runtime for 6809, 6309 and `SDC_NOVERCHK` at `ASM_ORG`, default `0x2000`, and prints its size), `make clean`. `make build` generates the example assets itself.

## Not yet verified on hardware

- The protocol comes from the CoCo SDC guide (as quoted in nowhereman999's `SDC_Stream_File_Library.asm` and VCC's stream emulation). The tests run against a model of the SDC, not a real one. You need STREAM-capable firmware (117 or later). `sdc_open` checks this.
- Drive 1 (`SDC_DRIVE equ 1` → `$E1`/`$91`) follows the command-bit pattern but hasn't been confirmed.
- `sdc_stop` waits for BUSY to clear after `$D0`. The guide says BUSY stays set until EOF or abort; the old library polled READY there instead.
- `SDC_DELAY` assumes the CPU runs at 1.79 MHz or slower. If you run a 6309 in native mode, raise it.

## License

zlib; see [LICENSE](LICENSE). Programs that assemble in the runtime don't need to include the notice; it only has to stay in copies of the source.
