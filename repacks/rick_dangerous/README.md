# Rick Dangerous on one dmpkg disk

`build/RICK.DSK` replaces the three original disks: one 805 KB image for the CoCo SDC. Mount it in drive 0 (`DRIVE 0,"RICK.DSK"`). `AUTOEXEC.BAS` runs `RICK.BIN`, which asks for the monitor type like the original `RICK.BAS` (`C` composite, `R` RGB) and then streams the whole game into RAM with dmpkg while the splash screen is up. There's no decompressing and no disk swapping.

It needs the same machine as the original (CoCo 3, 2 MB, H6309), plus a CoCo SDC with firmware 117 or later. `DIR` shows `RICK.DAT` (the game data, which BASIC must leave alone), `RICK.BIN` and `AUTOEXEC.BAS`.

```
make all        # extract, assets + project.yaml, include files, RICK.BIN, then RICK.DSK
make extract    # only copy the game files off bin/*.DSK into extracted/
make verify     # dmpkg verify
```

The only input is the three original disks in `bin/` (`RICK1.DSK`-`RICK3.DSK`); `extract.py` checks each against its SHA-256 and stops if one differs. Nothing outside this directory is used. You need python3, lwasm, and the dmpkg repo (`DMPKG=`, by default the repo this folder is in, `../..`). dmpkg must have `disk.reserve`, because the assets would fill every granule otherwise.

## How it works

- `extract.py` reads the Disk BASIC directory and FAT of `bin/RICK1-3.DSK` and copies out only the game files, `SPLASH0-3.BIN` and `COMP0000-0049.BIN`, into `extracted/`. The original loader (`RICK.BAS`, `MOVER.BIN`, `CC3X0.BIN`) is not used.
- `mkassets.py` runs the COMP stream through its own copy of the CC3X0 decompressor and merges the 205 blocks it writes into 44 contiguous physical runs. It writes each run as a raw `phys:` asset, with `SPLASH0-3` as the first asset (pages `$00`-`$03`). It also writes `project.yaml`.
- `loader.asm` (`RICK.BIN`, at `$1000` in page `$38`, the one page the game doesn't load) loads the splash and shows it, then loads every other asset. After that it recreates the hand-over the original loader left behind and jumps to the game's `Boot` at `$FD00`:
  - page `$38` copied to page `$3E` (MOVER StartGame)
  - task 1 = `$38,$30,$31,$32,$33,$3D,$35,$3F` (CC3X0 LaunchGame, from the stream header). The FIRQ sound handlers switch to task 1 and need slots 0 and 7, which the game never sets.
  - task 0 = `$38`-`$3F`, SAM RAM mode, fast CPU, B = 1 for composite (the page `$FF` boot stub)
- The loader first checks for a 6309 the way the game's `Boot` does (`TFR X,A` with X = `$0100`). On a 6809 it prints `THIS GAME REQUIRES A 6309 CPU` and returns to BASIC.
- The load runs at 1.79 MHz because the copy loop is CPU bound. It stays in 6809 emulation mode: in 6309 native mode the SDC can't keep up with the data reads on real hardware (`LOAD ERROR 4`, even with `DMPKG_DELAY=64`).
- Left out: the 7-byte stub in page `$38` and the boot stub in page `$FF`. They only pass control from the old loader to `Boot`.
- If a load fails, the screen shows `LOAD ERROR n` (n = dmpkg `DMPKG_E*` code) and the loader stops. BASIC has been overwritten by then, so it can't return.

## Tested

The loader was run off the built disk in dmpkg's test emulator (6809 + CoCo SDC model + `$FFA2` window) up to the jump to `$FD00`:

- all 203 stream blocks match `mem2m.bin` byte for byte
- the splash is in pages `$00`-`$03`, and page `$3E` equals page `$38`
- both MMU tasks, the palettes and B are correct for R and for C
- a stale build ID shows `LOAD ERROR 4`

On real hardware (CoCo 3, H6309, CoCo SDC) the game runs from this disk, and the whole load takes about 4 seconds at 1.79 MHz.
