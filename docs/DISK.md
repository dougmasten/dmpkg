# Single-file disk image

With `disk:` in the manifest ([MANIFEST.md](MANIFEST.md#projectdisk)), the image is also a standard 35-track DECB disk (161,280 bytes):

- **Assets:** the header, TOC and assets live in granules owned by a `DMPKG.DAT` file (`disk.data` names it something else), so Disk BASIC counts them as used and never overwrites them. No asset crosses track 17 (the directory track), because a stream can't skip it. First fit fills the space left before track 17 with later assets that fit.
- **Loader:** `disk.loader` (a LOADM file) and, by default, an `AUTOEXEC.BAS` that runs it (`10 LOADM"LOADER.BIN":EXEC`, tokenized unless `disk.tokenize: false`) go into the granules left over. SDC-DOS runs `AUTOEXEC.BAS` at power-on (hold SHIFT to skip it).
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

Asset placement and `build_id` don't depend on the loader, so pass 2 writes the same include files. `example/Makefile` does this for the example, whose `example/loader.asm` loads every asset and returns to BASIC.

## Disk map

`dmpkg list --map IMAGE` shows a disk image track by track: the DECB file that owns each granule, and the assets with bytes on each track, then what's past track 34 and how many granules are free. The image doesn't store asset names, so assets show as `#index` (their place in the TOC) unless `--names` points at the build's map report:

```
$ dmpkg list --map build/DEMO.DSK --names build/DEMO.map
Demo  build $6A821BD0  build/DEMO.DSK

track  granules                         data
    0   0:DMPKG.DAT     1:DMPKG.DAT     header+TOC, FONT, PALETTE, LEVEL1, MAP
    ...
   17  [ directory and FAT ]
    ...
```

It's a quick way to see whether a disk's assets are in load order (no stream restarts) and where the loader and `AUTOEXEC.BAS` went.

## Interrupts

Mask IRQ and FIRQ (`orcc #$50`) before `dmpkg_init`. After `LOADM`, Disk BASIC's 60 Hz interrupt counts down the drive-motor timer and writes `$FF40` when it runs out, which would take the SDC out of command mode in the middle of a stream.

## BASIC programs

`AUTOEXEC.BAS` is written as a tokenized program, the same bytes a `SAVE` would leave, unless `disk.tokenize` is `false` (then it's ASCII, which Disk BASIC tokenizes as it loads it). The tokenizer is also a command:

```sh
dmpkg tokenize PROG.TXT PROG.BAS [--base 0x2601]   # ASCII program -> tokenized file
dmpkg detokenize PROG.BAS [PROG.TXT]               # tokenized file -> listing
```

It follows the CoCo 3 ROM's tokenizer (Color BASIC's crunch routine with Super Extended BASIC's patch, per William Astle's disassemblies), so a program comes out as if it had been typed in:

- Keywords are tried at each position in table order, all commands first (Color, Extended, Disk 1.1, Super Extended), then all functions. The first match wins, so `LOCATE` is one command, not `LOC` plus `ATE`.
- Once a letter starts a variable name, the letters and digits after it aren't searched: `SCORE` stays `SCORE` (toolshed's `decb -t` makes it `SC OR E`), but `FORT` is `FOR` `T`.
- `?` is `PRINT`, `'` is stored as `:` and the `'` token, and `ELSE` gets a `:` in front. Text in quotes, after `REM` or `'`, and after `DATA` up to the next `:` is kept as typed. Keywords are upper case only.
- Lines are sorted by number, a repeated number replaces the earlier line, and a number on its own deletes it.

The file is `$FF`, a 2-byte length, then each line (next-line address, line number, tokens, `$00`) and `$00 $00`. The next-line addresses assume the program sits at `--base`: `$2601` is where Disk BASIC puts it with the usual `PCLEAR 4`, as in a `SAVE` from a stock machine.

