# Single-file disk image

With `disk:` in the manifest ([MANIFEST.md](MANIFEST.md#projectdisk)), the image is also a standard 35-track DECB disk (161,280 bytes):

- **Assets:** the header, TOC and assets live in granules owned by a `DMPKG.DAT` file (`disk.data` names it something else), so Disk BASIC counts them as used and never overwrites them. No asset crosses track 17 (the directory track), because a stream can't skip it. First fit fills the space left before track 17 with later assets that fit.
- **Loader:** `disk.loader` (a LOADM file) and, by default, an `AUTOEXEC.BAS` that runs it (`10 LOADM"LOADER.BIN":EXEC`) go into the granules left over. SDC-DOS runs `AUTOEXEC.BAS` at power-on (hold SHIFT to skip it).
- **No mount:** with no `sdc_path`, `DMPKG_MountName` is 0, and `dmpkg_open` streams the disk already mounted in `DMPKG_DRIVE`, the one the loader came from. Copy the `.DSK` anywhere on the card and `DRIVE 0,"DEMO.DSK"` it.
- **Bigger projects:** assets that don't fit carry on past track 34, where Disk BASIC never looks. When the assets would take every granule, set `disk.reserve` to keep some free for the loader and `AUTOEXEC.BAS` (`reserve: 2` for a loader under 2,304 bytes). If that takes the file past 720 sectors, it's padded to 737,792 bytes: the SDC treats 721–2880 sectors as a double-sided disk, which would move the directory track, and anything larger as a hard disk image that floppy emulation sees as single-sided.

The byte-level layout (FAT, directory, granules) is in [FORMAT.md](FORMAT.md#decb-disk-image).

## Two-pass build

The loader embeds `DMPKG_BuildID` and the descriptors, so it's assembled against the include files of the same build. Build twice:

```sh
dmpkg build project.yaml --no-loader   # pass 1: include files, disk without the loader
lwasm --6809 --format=decb -I asm -I build -o build/LOADER.BIN loader.asm
dmpkg build project.yaml               # pass 2: same layout, loader on the disk
```

Asset placement and `build_id` don't depend on the loader, so pass 2 writes the same include files. The `Makefile` does this for the example, whose `example/loader.asm` loads every asset and returns to BASIC.

## Interrupts

Mask IRQ and FIRQ (`orcc #$50`) before `dmpkg_open`. After `LOADM`, Disk BASIC's 60 Hz interrupt counts down the drive-motor timer and writes `$FF40` when it runs out, which would take the SDC out of command mode in the middle of a stream.
