# dmpkg runtime (`asm/dmpkg.asm`)

The 6809 runtime a program assembles in to load assets from a dmpkg image through the CoCo SDC `STREAM` command. It's 984 bytes, plain 6809, and assembles unchanged with `--6309`. Include it with the two files `dmpkg build` writes (see [MANIFEST.md](MANIFEST.md#symbols) for the symbols they define):

```asm
        jsr     dmpkg_init          ; once, at start: SDC there, firmware 117+
        bcs     error               ; A = error code
        ldx     #DMPKG_MountName    ; from assettab.asm (0 on a disk image: no mount)
        ldy     #DMPKG_BuildID      ; 0 to skip the stale-image check
        jsr     dmpkg_open
        bcs     error               ; A = error code
        ldx     #TITLE              ; asset descriptor
        jsr     dmpkg_load
        bcs     error
        ldx     #CODE               ; DECB asset
        jsr     dmpkg_load          ; U = exec address
        jsr     dmpkg_close         ; back to floppy emulation
        ...
        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
```

## Calls

| call | in | out |
|---|---|---|
| `dmpkg_open` | X = mount name, or 0 for the image already in `DMPKG_DRIVE`; Y = build_id or 0 | stream parked after the build_id |
| `dmpkg_init` | — | CoCo SDC found and its firmware at least `DMPKG_MINVER`; D = firmware version in BCD (`$0124`), also in `dmpkg_ver`. Leaves command mode on (`dmpkg_close` to leave) |
| `dmpkg_load` | X = descriptor | asset loaded; U = exec for decb |
| `dmpkg_getbyte` | — | A = next stream byte |
| `dmpkg_stop` | — | stream aborted (`$D0`), still in command mode |
| `dmpkg_close` | — | stopped, `$FF40` = 0 (floppy emulation back) |

On error, carry is set and A holds an error code (see [Error codes](#error-codes)). The stream is always stopped on error.

Call `dmpkg_init` once, at start, before anything else. It checks the SDC is there and its firmware has STREAM, and sets up the runtime's state, which is `rmb` (nothing is preset when the program loads). `dmpkg_open` doesn't repeat those checks: it enters command mode, ending a stream that's still running (found from the SDC's BUSY bit), so opening another image, or the same one again, without `dmpkg_close` is fine.

## Error codes

Every call returns carry clear on success. On failure it returns carry set and the code in A. The runtime defines the codes as `DMPKG_E*` equates, so compare against the names rather than the numbers.

| A | name | returned by | meaning | usual cause / fix |
|---|---|---|---|---|
| 1 | `DMPKG_ENODEV` | `dmpkg_init` | no CoCo SDC answered the presence check | no SDC plugged in, or the Multi-Pak switch is on another slot. Nothing was sent to the controller |
| 2 | `DMPKG_EVER` | `dmpkg_init` | firmware is older than `DMPKG_MINVER` (117, the first with STREAM) | update the SDC firmware. `dmpkg_ver` holds the version it found (BCD), or 0 if the firmware predates the VERSION command (before 113) |
| 3 | `DMPKG_EMOUNT` | `dmpkg_open` | the SDC couldn't mount the image (FAILED set after the name was sent) | the file isn't on the card at `sdc_path`, or the path or name is wrong. Check that the card has `GAMES/DEMO.SDS` (or your path) |
| 4 | `DMPKG_EHDR` | `dmpkg_open` | the image's magic isn't `DMPKG_MAGIC` (`DMPK`), or its `build_id` doesn't match `DMPKG_BuildID` | wrong file mounted, or a stale image: the card and the program come from different builds. Copy the current image to the card, or reassemble against it |
| 5 | `DMPKG_EEOF` | `dmpkg_open`, `dmpkg_load`, `dmpkg_getbyte` | BUSY dropped while waiting for READY: the stream reached end of file before the data | the image is truncated or isn't the one the descriptors were built for. Usually only seen with `Y = 0`, where the stale-image check is skipped |
| 6 | `DMPKG_ETIME` | any call | BUSY or READY didn't change within `DMPKG_TIMEOUT` × 65536 polls | no SDC answering (missing, or wrong Multi-Pak slot), or a stuck controller. Raise `DMPKG_TIMEOUT` only if a slow card really needs it |
| 7 | `DMPKG_EFAIL` | `dmpkg_open`, `dmpkg_load`, `dmpkg_getbyte` | the controller reported an error: FAILED (status bit 7) set, or the stream ended (BUSY cleared) with status `$08` instead of `$00`. The firmware ends a failed stream with `$08` and doesn't set FAILED, contrary to the User Guide | SD card read error, card removed, or the controller rejected the command |

`dmpkg_init` fails with `DMPKG_ENODEV`, `DMPKG_EVER` or `DMPKG_ETIME`. `dmpkg_stop` and `dmpkg_close` always succeed.

## Presence and firmware checks

**Presence check:** `dmpkg_init` first writes `$64`, then `$00`, to the Flash data register (`$FF42`) and reads the Flash control register (`$FF43`) after each write. On a CoCo SDC, bits 5–6 of `$FF43` follow the data register, so the two reads differ by exactly `$60`. Anything else means no SDC, and it returns error 1 at once, without sending a command or waiting for a timeout. The check doesn't set the Flash program bit or change the bank, so it's safe with code running from the cartridge ROM. The method comes from the FUZIX CoCo SDC driver (`devsdc_exist`); the User Guide doesn't describe one.

**Firmware check:** `dmpkg_init` then sends the SDC `VERSION` command (`$C0`, `$FF49` = `'V'`). The command arrived in firmware 113, and older firmware answers FAILED, which counts as version 0. `STREAM` arrived in 117. If the version is below `DMPKG_MINVER` (`$0117`), it returns error 2 before anything is mounted. `dmpkg_ver` keeps the version it read, so you can report it. [SDC_FIRMWARE.md](SDC_FIRMWARE.md) lists what each firmware version added.

## Loading

`dmpkg_load` skips the restart when the stream is already sitting at the asset. Otherwise it restarts at the asset's block and skips its offset. Copies run in chunks capped by three things: the end of the 512-byte block, the bytes left, and the end of the MMU window. Inside a chunk the inner loop copies 32 bytes per pass: 16 × `LDD $FF4A / STD n,U` with 5-bit offsets (U points at the middle of the group), then one `LEAU 32,U` and a `DEC`/`BNE` group counter, about 6 cycles per byte. The 0–31 byte tail goes 2 bytes at a time. Odd offsets and odd lengths take one byte from `$FF4B` or `$FF4A`, so the A-then-B read order is always kept.

**Phys loads:** the asset is mapped through the 8K window at `DMPKG_WINDOW` (default `$4000–$5FFF`). Its MMU register `DMPKG_MMUREG` is worked out from the window as the task 0 register `$FFA0 + DMPKG_WINDOW/$2000` (`$FFA2` by default). Define `DMPKG_MMUREG` yourself to use task 1. It must be an MMU register (`$FFA0–$FFAF`) for the same 8K slot as the window, so `$FFA2` or `$FFAA` for `$4000`. The window must be 8K-aligned and between `$2000` and `$C000`, or the include stops with an assembly error. The next 8K block is mapped every time the window fills. Afterwards the register is put back to the value it held before. If your board doesn't read MMU registers back cleanly, define `DMPKG_MMU_HOME`. Keep code, stack and the descriptor out of the window.

## Configuration

Configure these before the include: `DMPKG_DRIVE` (0), `DMPKG_WINDOW` ($4000), `DMPKG_MMUREG` (from `DMPKG_WINDOW`), `DMPKG_MMU_HOME`, `DMPKG_DELAY` (12), `DMPKG_TIMEOUT` (16), `DMPKG_MINVER` ($0117). The runtime is plain 6809 and assembles unchanged with `--6309`. It uses `DMPKG_MAGIC` from `assets.inc`. If you assemble it without an unprefixed `assets.inc`, define it yourself: `DMPKG_MAGIC equ $444D504B`.

On a disk image, mask interrupts before `dmpkg_init`; see [DISK.md](DISK.md#interrupts).
