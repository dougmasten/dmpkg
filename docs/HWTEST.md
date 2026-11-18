# Hardware test

The tests in [TESTING.md](TESTING.md) run against a model of the CoCo SDC. This page describes a test for a real one. It covers the items under [Not yet verified on hardware](TESTING.md#not-yet-verified-on-hardware) that a loader can check by itself:

- the presence check and the version query
- STREAM from the disk SDC-DOS mounted with `DRIVE`
- `AUTOEXEC.BAS` starting the loader
- mounting a stream image by name
- `sdc_stop` part way through a block
- phys loads through the MMU on a CoCo 3
- reading past track 34 of a disk padded to hard-disk size

## Build

```sh
make hwtest
```

This writes three images to `hwtest/build/`:

| file | what it is |
|---|---|
| `HWTEST.DSK` | 35-track disk: `HWTEST.BIN`, `AUTOEXEC.BAS`, and the test assets in `DMPKG.DAT` |
| `HWBIG.DSK` | the same assets plus fillers, so a `TAIL` asset lands past track 34. The image is padded to 737,792 bytes (the size the SDC takes as a hard disk image) |
| `HWTEST.SDS` | a bare stream image that the loaders mount by name |

`make test` builds them too, and runs both loaders against the SDC model first, on a CoCo 2 and a CoCo 3. That covers a clean run, a corrupted asset, and a missing `HWTEST.SDS`.

## Run

You need:

- CoCo SDC firmware 1.17 or later
- SDC-DOS 1.3 or later for `AUTOEXEC.BAS`; on older SDC-DOS, type `LOADM"HWTEST":EXEC`
- 32K of RAM or more

Steps:

1. Copy all three files into **one folder** on the SD card. The loaders mount `HWTEST.SDS`, and then their own disk, by name with no path, so the SDC's current directory must be that folder. Use `DIR "folder"` in SDC-DOS, or the card root.
2. `DRIVE 0,"HWTEST.DSK"`, then power-cycle or cold-start. `AUTOEXEC.BAS` runs the test. Or type `LOADM"HWTEST":EXEC`.
3. Read the screen, then run the big disk the same way: `DRIVE 0,"HWBIG.DSK"`, then `LOADM"HWBIG":EXEC` (or cold-start).

A run takes a second or two and returns to BASIC. `A` holds the number of failures, but BASIC doesn't show it, so read the screen.

On a CoCo 3, also try both disks at double speed: `POKE 65497,0` before `LOADM`. The runtime's settle delay (`SDC_DELAY`) is sized for 0.89 MHz to 1.79 MHz, so this is the case most likely to fail.

## Reading the screen

```
DMPKG HARDWARE TEST  HWTEST
SDC FW     1.28 COCO 3
BAD ID     OK   OPEN       OK
STOP       OK   A_ODD      OK
B_PACK     OK   C_MULTI    OK
D_ALIGN    OK   E_CODE     OK
EXEC       OK   F_PHYS     OK
RELOAD B   OK   MOUNT SDS  OK
S_DATA     OK   S_ALIGN    OK
NO FILE    OK   REMOUNT    OK
ALL PASSED
```

Each cell shows a result:

| result | meaning |
|---|---|
| `OK` | the check passed |
| `SKIP` | not run: `F_PHYS` on a CoCo 1 or 2, `EXEC` when `E_CODE` failed |
| `BAD` | the asset loaded without an error, but its bytes don't match their checksum |
| `E1`–`E7` | the call returned that error code (see [RUNTIME.md](RUNTIME.md#error-codes)) |
| `E0` | the call should have failed and didn't |

Failures show in inverse video (dark on green) and are counted in the last line, `n FAILED`.

| cell | what it checks |
|---|---|
| `SDC FW` | `sdc_init`: the presence check found the SDC, and the firmware version it reported. If it shows `E1`, the presence check failed. `E6` means the controller didn't answer. Either way the test stops there |
| `COCO` | CoCo 3 (reset vector `$8C1B`) or CoCo 1/2. Only the CoCo 3 runs `F_PHYS` |
| `BAD ID` | `sdc_open` on the mounted disk with the wrong build_id returns `E4` |
| `OPEN` | `sdc_open` with no mount streams the disk SDC-DOS mounted in drive 0. If this fails, the test stops |
| `STOP` | three `sdc_getbyte`, then `sdc_stop` (`$D0` part way through a block, then waiting for BUSY to clear) |
| `A_ODD` … `E_CODE` | each asset: its memory is zeroed, then `sdc_load`, then the bytes are checked. `B_PACK` starts at an odd offset at an odd address, `C_MULTI` spans three blocks, `D_ALIGN` is block-aligned, and `E_CODE` (a DECB file in `D_ALIGN`'s alignment gap) is a seek backwards |
| `EXEC` | calls `E_CODE`'s exec address, which must return `D = $600D` |
| `F_PHYS` | 20,001 bytes to physical `$060001`, across three 8K MMU windows. Physical `$60000–$6FFFF` is the HSCREEN area, unused at power-on |
| `TAIL` | `HWBIG.DSK` only: 10,000 bytes from past track 34, in the padded part of the image |
| `RELOAD B` | `B_PACK` again, out of order: the stream restarts at its block and skips to its offset |
| `MOUNT SDS` | `sdc_open "M:HWTEST.SDS"` mounts by name, with its build_id checked. `E3` means the file isn't in the SDC's current directory |
| `S_DATA`, `S_ALIGN` | the stream image's two assets |
| `NO FILE` | `sdc_open "M:NOSUCH.SDS"` returns `E3` |
| `REMOUNT` | `sdc_open "M:HWTEST.DSK"` (or `HWBIG.DSK`) puts the disk back in drive 0 for Disk BASIC. If it fails, type `DRIVE 0,"HWTEST.DSK"` before using the disk |

The checksums come from `hwtest/mkhw.py`. They're Fletcher-style sums, so they catch bytes in the wrong order or place, not just wrong bytes.

## Not covered

- **Drive 1** (`SDC_DRIVE equ 1`). The loader is built for drive 0.
- **The no-SDC path** (`E1` without waiting for a timeout) on a machine with a regular floppy controller and no SDC. The test disk can't be run from such a machine without the SDC, so this needs a real floppy holding `HWTEST.BIN`.
- **Firmware older than 1.17** (`E2`). Getting it would mean downgrading the SDC.

## Files

| file | what it is |
|---|---|
| `hwtest/hwtest.asm` | the loader. `-DHWBIG` builds the `HWBIG.DSK` one |
| `hwtest/disk.yaml`, `big.yaml`, `stream.yaml` | the three manifests. The stream image's symbols have an `S_` prefix, so the loaders can include them next to their own |
| `hwtest/mkhw.py` | writes the assets (fixed seed) and `build/hwsums.inc`, the checksum table. It sizes `big.yaml`'s `GAP` filler from `disk.yaml`'s layout |
