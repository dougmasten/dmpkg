# Stress test

`STRESS.DSK` streams the same 512K again and again on a real CoCo SDC and checks every byte, to catch errors that only turn up now and then, like an occasional `LOAD ERROR 4`.

Each pass:

1. opens the disk, so the header and build_id check runs every pass
2. loads the 64 assets of 8K one after another into `$4000-$5FFF`, in image order
3. checks each asset's Fletcher sums against the table `mkstress.py` wrote

Asset k holds a rolling pattern: byte i is `(i + i/256 + 37k) mod 256`, so a dropped, repeated or swapped byte, or the wrong asset, changes it. After a bad sum, the loader finds the first wrong byte and shows the asset, its offset, the byte it read and the one it wanted. Errors from `dmpkg_open` and `dmpkg_load` are counted by code, and the pass starts over.

```sh
make            # build build/STRESS.DSK and verify it
make verify
make clean
```

## Run

You need CoCo SDC firmware 1.17 or later and 32K or more.

1. Copy `STRESS.DSK` to the SD card and mount it on its own: `DRIVE 0,"STRESS.DSK"`.
2. Cold-start (`AUTOEXEC.BAS` runs it) or type `LOADM"STRESS":EXEC`. On a CoCo 3, `POKE 65497,0` first to test at 1.79 MHz.
3. Leave it running for a few hundred passes, then press any key. It returns to BASIC.

A pass takes roughly 10 seconds at 1.79 MHz and twice that at 0.89 MHz. A clean run has `PASSES` going up and every other count at 0.

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

What each line means: [docs/HWTEST.md](../../docs/HWTEST.md#stress-test). The error codes are in [docs/RUNTIME.md](../../docs/RUNTIME.md#error-codes).

## Files

| file | what it is |
|---|---|
| `stress.asm` | the loader (`STRESS.BIN`) |
| `stress.yaml` | the manifest: 64 assets of 8K, each loaded to `$4000` |
| `mkstress.py` | writes the 64 pattern assets and `build/ssums.inc`, their checksum table |
| `Makefile` | runs `mkstress.py`, then builds the disk in two passes |
