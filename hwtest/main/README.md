# Hardware test

One pass over the things a loader can check for itself on a real CoCo SDC. Each check gets a cell on the screen with its result:

- the presence check and the firmware version
- STREAM from the disk SDC-DOS mounted with `DRIVE`, with `AUTOEXEC.BAS` starting the loader
- assets at odd lengths and offsets, across blocks, aligned, a DECB file with two segments
- phys loads through the MMU window (CoCo 3)
- `dmpkg_stop` part way through a block, and reloading out of order
- the time to stream a block and the extra time a restart costs (`BLOCK MS`, `RESTART MS`)
- mounting a stream image by name, a missing file, and putting the disk back
- on the big disk, an asset past track 34 of an image padded to hard-disk size

```sh
make            # build build/HWTEST.SDS, HWTEST.DSK, HWBIG.DSK and verify them
make verify
make clean
```

## Run

You need CoCo SDC firmware 117 or later, SDC-DOS 1.3 or later for `AUTOEXEC.BAS`, and 32K or more.

1. Copy the three images into **one folder** on the SD card, and make it the SDC's current directory (`DIR "folder"`, or use the card root). The loaders mount `HWTEST.SDS`, and then their own disk again, by name with no path.
2. `DRIVE 0,"HWTEST.DSK"`, then cold-start (`AUTOEXEC.BAS` runs it) or type `LOADM"HWTEST":EXEC`.
3. Read the screen, then do the same with `HWBIG.DSK` (`LOADM"HWBIG":EXEC`).

A run takes a few seconds and returns to BASIC. Failures show in inverse. On a CoCo 3, try both disks at 1.79 MHz too: `POKE 65497,0` before `LOADM`.

What each cell means, the timing, and what isn't covered: [docs/HWTEST.md](../../docs/HWTEST.md).

## Files

| file | what it is |
|---|---|
| `hwtest.asm` | the loader; `-DHWBIG` builds the big disk's |
| `disk.yaml`, `big.yaml`, `stream.yaml` | the three manifests |
| `mkhw.py` | writes the assets (random filler, fixed seed) and `build/hwsums.inc`, the checksums the loader checks them against |
| `Makefile` | builds `HWTEST.SDS` first (the disks' loaders include its symbols), then each disk in two passes |
