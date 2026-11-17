# CoCo SDC firmware versions

What each CoCo SDC MCU firmware version added, and which of those features dmpkg relies on. `sdc_version` returns the version in BCD, so 1.17 comes back as `$0117` (the version command answers `$01` in `$FF4A` and `$17` in `$FF4B`).

The firmware entries come from the official `Changes.pdf` that ships with each firmware package. They're quoted closely, with notes from a disassembly of the MCU firmware where it shows more. SDC-DOS (the Disk BASIC in the controller's flash) has its own version numbers and is listed separately.

## What dmpkg needs

| version | feature | used for |
|---|---|---|
| 1.17 | `STREAM` command (`$90`/`$91`) | everything `sdc_load` does. `sdc_open` refuses older firmware with `SDC_EVER` (`SDC_MINVER`, default `$0117`) |
| before 1.17 | `VERSION` query (`$C0`, `$FF49` = `'V'`) | the firmware check. Firmware without it answers FAILED, and `sdc_version` returns 0. The runtime takes 1.13 as the first version with it; the official change list starts at 1.15, so that's unconfirmed |
| SDC-DOS 1.3 | `AUTOEXEC.BAS` at power-on | starting the loader on a [disk image](DISK.md). Optional: without it, run the loader by hand |

Two firmware behaviours that differ from the User Guide, both seen in the disassembly and handled by the runtime:

- A failed stream (abort, start past the end of the file, card error) ends with status `$08` and **no** FAILED bit. `sdc_load` treats a stream that ends with `$08` as `SDC_EFAIL`.
- Images with a header (JVC, VDK), SDF images and VDKs that skip a sector aren't streamed: the command ends at once with status `$00`. dmpkg images are headerless, so this doesn't come up.

## MCU firmware

| version | date | changes |
|---|---|---|
| 1.29 | Oct 6, 2025 | SDC Explorer could report *Directory Not Found* in folders extracted from some archives (the `.` and `..` entries had the hidden attribute). New files dated Oct 6, 2025 |
| 1.28 | Feb 22, 2024 | Fixes two 1.27 regressions: the NitrOS-9 SDC driver failed under 6309 native Level 2, and VDK images with a 256-byte header couldn't be used. New files dated Feb 22, 2024 |
| 1.27 | Feb 27, 2023 | More robust FDC emulation with SDF images (CRC errors and deleted data marks reported, early CRC in ID fields recognised when formatting). Command to switch to a specific image in a disk set (`$C0 '#'`). 8-bit transfer option for Write Logical Sector, so a 6309 `TFM` can be used. Mount or create files that aren't disk images (`m:` and `n:`): no size or header checks, reached only through the LBA sector commands or `STREAM`. New files dated Feb 27, 2023 |
| 1.22–1.26 | — | never released: the change list goes from 1.21 straight to 1.27 |
| 1.21 | May 30, 2020 | Single-density formatting of SDF images fixed. New files dated May 30, 2020 |
| 1.20 | Jul 24, 2017 | "Query Disk Size" (`$C0 'Q'`) returned only the low 16 bits of its 24-bit result. New files dated Jul 24, 2017 |
| 1.19 | Jun 9, 2017 | Timing bug in floppy emulation on a CoCo 3 at double speed with a 6309 in native mode. New files dated Jun 9, 2017 |
| 1.17 | Apr 16, 2017 | **`STREAM` I/O command**, "to aid in the development of Media Player programs". New files dated the firmware's build date; an image's modification time goes up 2 seconds if it was written to during a session |
| 1.16 | Dec 4, 2015 | VDK headers of 12 to 256 bytes recognised (1.15 took only 12) |
| 1.15 | — | JVC and VDK image headers. Dragon DOS controller (WD2797) emulation with the DRGN switch (and the DRQ jumper). Firmware FAT operations: delete, rename, make directory, query file size (through DSKCON or the hardware only). `D=` current directory line in `STARTUP.CFG` |

1.18 isn't in the change list. The official list doesn't go back before 1.15. SDC-DOS's `SETUP` needs MCU firmware 1.10 or later to install an update.

## SDC-DOS

| version | date | changes |
|---|---|---|
| 1.75 | Feb 27, 2023 | `DEF STEP = r` sets the step rate of real floppy drives (6, 12, 20 or 30 ms). `BACKUP` and a new `DSKINI` keep the program in memory; `DSKINI` marks granules with bad sectors as allocated (up to 6). Sector I/O polls status instead of using HALT and NMI, which avoids problems on CoCo 1 and 2 (no pull-ups on the address bus and R/W lines, especially with a 6309) |
| 1.6 | Oct 21, 2017 | `DSKINI` on real floppies took minutes to verify |
| 1.5 | Aug 19, 2017 | Floppy motors could stay on after `BACKUP` or `COPY` from a real floppy to the SD card |
| 1.4 | Dec 9, 2016 | Conflict with Kilgus Color TRSDOS (Disk EDTASM, EDTASM6309) fixed. Floppy motors could stay on with the SDC and a real floppy controller in an MPI |
| 1.3 | Dec 1, 2016 | **`AUTOEXEC.BAS`** loaded and run from the mounted disks at power-on or cold reset (hold SHIFT to skip). `EXP` mounts `SDCEXP.DSK` (SDC Explorer) in drive 1. `DEF DW = n` sets the DriveWire speed. `WRITE MEM` and `COPY MEM` reach flash `$FExx` on a CoCo 3. `RUN @bank` rewritten for better compatibility. `DSKINI` on a new SDF image could give `?IO ERROR` |
| 1.2 | Jul 17, 2014 | Current directory (`DIR = "path"`). Paged `DIR` listing. `NEW+` makes a 40-track SDF image, `NEW++` a 40-track double-sided one |

## Possible future use

1.27's `m:` mounts any file as raw sectors, with no size check. A stream image mounted that way wouldn't need padding to `$14400` bytes (`min_size`), at the cost of requiring 1.27. dmpkg doesn't use it yet: `M:` works from 1.17 on.
