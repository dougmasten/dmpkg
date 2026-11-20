# Hardware test

The tests in [TESTING.md](TESTING.md) run against a model of the CoCo SDC. This page describes a test for a real one. It covers the items under [Not yet verified on hardware](TESTING.md#not-yet-verified-on-hardware) that a loader can check by itself:

- the presence check and the version query
- STREAM from the disk SDC-DOS mounted with `DRIVE`
- `AUTOEXEC.BAS` starting the loader
- mounting a stream image by name
- `dmpkg_stop` part way through a block
- phys loads through the MMU on a CoCo 3
- reading past track 34 of a disk padded to hard-disk size

It also times how long a stream restart takes compared with streaming a block, which the model can't tell us. That decides whether loading assets in image order is worth doing (see [Timing](#timing)).

## Build

```sh
make hwtest                 # both tests: runs hwtest/Makefile
cd hwtest/main && make      # or just the hardware test
cd hwtest/stress && make    # or just the stress test
```

Each test has its own folder and Makefile (`make`, `make verify`, `make clean`). The hardware test writes three images to `hwtest/main/build/`, and the stress test one to `hwtest/stress/build/`:

| file | what it is |
|---|---|
| `HWTEST.DSK` | 35-track disk: `HWTEST.BIN`, `AUTOEXEC.BAS`, and the test assets in `DMPKG.DAT` |
| `HWBIG.DSK` | the same assets plus fillers, so a `TAIL` asset lands past track 34. The image is padded to 737,792 bytes (the size the SDC takes as a hard disk image) |
| `HWTEST.SDS` | a bare stream image that the loaders mount by name |
| `STRESS.DSK` | the [stress test](#stress-test): 64 assets of 8K that `STRESS.BIN` streams and checks over and over |

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

A run takes a few seconds and returns to BASIC. `A` holds the number of failures, but BASIC doesn't show it, so read the screen.

On a CoCo 3, also try both disks at double speed: `POKE 65497,0` before `LOADM`. The runtime's settle delay (`DMPKG_DELAY`) is sized for 0.89 MHz to 1.79 MHz, so this is the case most likely to fail.

## Reading the screen

```
DMPKG HARDWARE TEST  HWTEST
SDC FW     1.28 COCO 3
BAD ID     OK   OPEN       OK
STOP       OK   A_ODD      OK
B_PACK     OK   C_MULTI    OK
D_ALIGN    OK   E_CODE     OK
EXEC       OK   F_PHYS     OK
RELOAD B   OK   BLOCK MS   x.x
RESTART MS x.x  MOUNT SDS  OK
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
| a number | `BLOCK MS` and `RESTART MS` only: a time in ms, not a pass or fail |

Failures show in inverse video (dark on green) and are counted in the last line, `n FAILED`.

| cell | what it checks |
|---|---|
| `SDC FW` | `dmpkg_init`: the presence check found the SDC, and the firmware version it reported. If it shows `E1`, the presence check failed. `E6` means the controller didn't answer. Either way the test stops there |
| `COCO` | CoCo 3 (reset vector `$8C1B`) or CoCo 1/2. Only the CoCo 3 runs `F_PHYS` |
| `BAD ID` | `dmpkg_open` on the mounted disk with the wrong build_id returns `E4` |
| `OPEN` | `dmpkg_open` with no mount streams the disk SDC-DOS mounted in drive 0. If this fails, the test stops |
| `STOP` | three `dmpkg_getbyte`, then `dmpkg_stop` (`$D0` part way through a block, then waiting for BUSY to clear) |
| `A_ODD` … `E_CODE` | each asset: its memory is zeroed, then `dmpkg_load`, then the bytes are checked. `B_PACK` starts at an odd offset at an odd address, `C_MULTI` spans three blocks, `D_ALIGN` is block-aligned, and `E_CODE` (a DECB file in `D_ALIGN`'s alignment gap) is a seek backwards |
| `EXEC` | calls `E_CODE`'s exec address, which must return `D = $600D` |
| `F_PHYS` | 20,001 bytes to physical `$060001`, across three 8K MMU windows. Physical `$60000–$6FFFF` is the HSCREEN area, unused at power-on |
| `TAIL` | `HWBIG.DSK` only: 10,000 bytes from past track 34, in the padded part of the image |
| `RELOAD B` | `B_PACK` again, out of order: the stream restarts at its block and skips to its offset |
| `BLOCK MS`, `RESTART MS` | the [timing](#timing) results. `BLOCK MS` shows `BAD` or an error code if a timing load failed (counted as a failure), or `NOIRQ` if the 60 Hz interrupt never came (not counted). `RESTART MS` then shows `SKIP` |
| `MOUNT SDS` | `dmpkg_open "M:HWTEST.SDS"` mounts by name, with its build_id checked. `E3` means the file isn't in the SDC's current directory |
| `S_DATA`, `S_ALIGN` | the stream image's two assets |
| `NO FILE` | `dmpkg_open "M:NOSUCH.SDS"` returns `E3` |
| `REMOUNT` | `dmpkg_open "M:HWTEST.DSK"` (or `HWBIG.DSK`) puts the disk back in drive 0 for Disk BASIC. If it fails, type `DRIVE 0,"HWTEST.DSK"` before using the disk |

The checksums come from `hwtest/main/mkhw.py`. They're Fletcher-style sums, so they catch bytes in the wrong order or place, not just wrong bytes.

## Timing

`BLOCK MS` is the time to stream one 512-byte block, and `RESTART MS` is the extra time a restart costs: going backwards in the image means the runtime has to abort the stream and start a new one.

The loader measures them with two runs over the same 128 blocks, timed by the video's 60 Hz field-sync interrupt (PIA0 CB1):

1. `T_BLK` (one aligned block) loaded 128 times. Each load goes backwards, so this is 128 restarts plus 128 blocks: R ticks.
2. `T_RUN` (16 aligned blocks) loaded 8 times: 8 restarts plus 128 blocks, S ticks.

A restart is (R − S) / 120 ticks, and a block is (S − 8 × restart) / 128. A tick is 16.67 ms. On a 50 Hz (PAL) machine it's 20 ms, so multiply both numbers by 1.2. Each run can be a tick out at each end, so `RESTART MS` is good to about ±0.3 ms. The loaded bytes are checked after each run.

For the timing only, the loader points BASIC's IRQ hook at `$010C` to its own tick counter and turns on the field-sync IRQ (`$FF03` bit 0). It unmasks IRQ but not FIRQ, and puts everything back afterwards. The numbers `make test` gets from the model don't reflect real hardware.

How to read them: if `RESTART MS` is close to `BLOCK MS`, or smaller, then the restarts that first-fit placement can cause cost a few ms each, and image-order loading isn't worth doing. If it's tens of ms, it is. Compare the numbers at normal speed and, on a CoCo 3, at double speed.

## Stress test

`STRESS.DSK` streams the same 512K again and again and checks every byte, to catch errors that only turn up now and then (like an occasional `LOAD ERROR 4`). Copy it to the card and mount it on its own: `DRIVE 0,"STRESS.DSK"`, then `RUN"AUTOEXEC"` or `LOADM"STRESS":EXEC`. It runs until you press a key, then returns to BASIC.

Each pass opens the disk, so the header and build_id check runs every pass. Then it loads the 64 assets one after another into `$4000-$5FFF`, in image order, so it's one stream except where it jumps to the first asset and past track 17. Asset k holds a rolling pattern: byte i is `(i + i/256 + 37k) mod 256`, so a dropped, repeated or swapped byte, or the wrong asset, changes it. After each load the loader takes the asset's Fletcher sums (as in the hardware test) and compares them with the table `hwtest/stress/mkstress.py` wrote.

```
DMPKG STRESS TEST  ANY KEY STOPS

PASSES        123
BAD SUM         0
OPEN ERRORS     0
LOAD ERRORS     0

E1     0  E2     0  E3     0
E4     0  E5     0  E6     0
E7     0

ASSET    OFF $
GOT $    WANT $

LAST ERROR
```

- `PASSES`: whole passes of all 64 assets.
- `BAD SUM`: assets whose sums were wrong. For the last one, `ASSET` and `OFF` give the asset and the offset of its first wrong byte, and `GOT` and `WANT` the byte read and the one the pattern wanted (`OFF $FFFF` means the sums were off but every byte matched).
- `OPEN ERRORS`, `LOAD ERRORS`: errors from `dmpkg_open` (and `dmpkg_init`) and from `dmpkg_load`, also counted by code under `E1`-`E7` ([error codes](RUNTIME.md#error-codes)). `LAST ERROR` says where the last one happened. After an error the pass starts over.

A pass takes roughly 10 seconds at 1.79 MHz and twice that at 0.89 MHz (18 million cycles in the model, whose SDC timing is only a guess). Try both speeds (`POKE 65497,0` before `LOADM` for 1.79 MHz on a CoCo 3), and leave it running for a few hundred passes. A clean run has `PASSES` going up and every other count at 0.

`make test` runs the stress loader against the SDC model too: a clean pass, a byte corrupted on the disk (found, with its offset and both bytes), and a stale build_id (`E4` at open).

## Not covered

- **Drive 1** (`DMPKG_DRIVE equ 1`). The loader is built for drive 0.
- **The no-SDC path** (`E1` without waiting for a timeout) on a machine with a regular floppy controller and no SDC. The test disk can't be run from such a machine without the SDC, so this needs a real floppy holding `HWTEST.BIN`.
- **Firmware older than 1.17** (`E2`). Getting it would mean downgrading the SDC.

## Files

| file | what it is |
|---|---|
| `hwtest/Makefile` | builds and verifies both tests (`make hwtest` at the top runs it) |
| `hwtest/main/hwtest.asm` | the loader. `-DHWBIG` builds the `HWBIG.DSK` one |
| `hwtest/main/disk.yaml`, `big.yaml`, `stream.yaml` | the three manifests. `T_BLK` and `T_RUN` are the timing assets, both loaded to `$4000`. The stream image's symbols have an `S_` prefix, so the loaders can include them next to their own |
| `hwtest/main/mkhw.py` | writes the assets (fixed seed) and `build/hwsums.inc`, the checksum table. It sizes `big.yaml`'s `GAP` filler from `disk.yaml`'s layout |
| `hwtest/main/Makefile` | builds `HWTEST.SDS` first (the disks' loaders include its symbols), then each disk in two passes |
| `hwtest/stress/stress.asm`, `stress.yaml`, `mkstress.py`, `Makefile` | the stress test: its loader, its manifest, the generator for its 64 pattern assets and `build/ssums.inc` (their checksum table), and its Makefile |
