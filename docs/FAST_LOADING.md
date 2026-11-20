# Fast loading

How dmpkg gets data off a CoCo SDC quickly, and what a loader can do to stay fast. The numbers come from the test emulator unless they say real hardware. Where the emulator can't say (the SDC's own timing), the [hardware test](HWTEST.md) measures it on your machine.

## Why dmpkg is fast

**One command streams the whole image.** The SDC's `STREAM` command (firmware 117 and later) sends 512-byte blocks one after another for as long as the program keeps reading. There's no command, seek or sector number per block. Disk BASIC's `LOADM`, by contrast, goes through the SDC's floppy emulation: a 256-byte sector at a time through `DSKCON` and a sector buffer, then the bytes handed to the loader one by one, with directory and granule lookups for every file.

**Everything is one file, laid out in load order.** The packer puts every asset in a single image, so a load is one stream from start to finish. There are no files to open and no directory to search, and there are no disk swaps. The packer works out every asset's block, offset, length and destination in advance (the descriptors in `assettab.asm`), so the runtime never searches for anything.

**Bytes go straight to their destination.** The runtime copies from the SDC's data registers (`$FF4A`/`$FF4B`, two bytes per read) to where the asset belongs, with no buffer in between. CoCo 3 physical addresses go through an 8K MMU window, so even data outside the CPU's 64K needs no second copy.

**The copy loop is unrolled.** The inner loop copies 32 bytes a pass, 16 times `LDU ,Y` / `STU n,X` with 5-bit offsets, then `ABX` and a `DECA`/`BNE` counter. That's about 5.7 cycles per byte, roughly 1.6 ms of CPU time per 512-byte block at 1.79 MHz. Polling for READY happens once per block, not per byte.

**Nothing else competes for the CPU.** Interrupts are masked during a load (they have to be anyway, see [DISK.md](DISK.md#interrupts)), and raw assets need no unpacking or record parsing.

On real hardware, Rick Dangerous (805K, three original disks) loads in about 6 seconds at 0.89 MHz and about 4 seconds at 1.79 MHz.

## STREAM versus sector reads

The CoCo SDC's command mode has two ways to read an image. The details below come from the CoCo SDC User Guide and a disassembly of the firmware; [SDC_FIRMWARE.md](SDC_FIRMWARE.md) lists which firmware added what.

| | Read logical sector (`$8n`) | `STREAM` (`$9n`) |
|---|---|---|
| Data per command | one 256-byte sector | every 512-byte block from the start block to the end of the file |
| Per block of data | for each sector: write the 24-bit LSN to `$FF49-$FF4B`, write the command, leave the registers alone for 20 µs, poll READY, read the sector, poll until BUSY clears | poll READY, read the block |
| Commands for 512 bytes | 2 | none after the first |
| Order | any sector, any order | forward only; going anywhere else means `$D0` (abort) and a new `STREAM` |
| Writes | yes (`$An`) | no |
| Images | any, including JVC/VDK headers and SDF | headerless images and raw files only; others end the command at once |
| Firmware | all versions | 1.17 and later |

Both read the data the same way: pairs from `$FF4A`/`$FF4B`, 2 bytes per `LDD` or `LDU`. So the CPU's copy costs the same either way. The difference is everything around it.
- **Sector reads:** every 256 bytes pay for a full command: setting it up, the 20 µs the guide asks for after it, the SDC finding the sector on the card, and the final BUSY wait.
- **`STREAM`:** pays for that once per load, plus once for each restart.

A dmpkg restart (abort, then a new `STREAM`) costs about the same as one sector command, so the hardware test's `RESTART MS` cell is a fair estimate of what each sector read would add. Reading the same data by sectors would cost one of those every 256 bytes. Streaming costs one per load, plus one per restart.

Disk BASIC is slower again. It doesn't use command mode at all: it reads through the SDC's emulation of the WD179x floppy controller, with `DSKCON` working out track and sector, a sector buffer, and `LOADM` taking the bytes from there one at a time.

What sector reads have going for them is random access and writing: a program that reads a little from many places, or saves data, needs them. dmpkg only loads, and the packer decides the order in advance, so it can use `STREAM` and lay the image out to be read straight through.

## Tips for a fast loader

### Load in image order

A stream only goes forward. When the next asset starts where the last one ended, `dmpkg_load` just carries on. Anything else is a *restart*: the stream stops and a new one starts at the asset's block, then skips to its offset. Each restart costs time (1.4 ms in the emulator; the hardware test's `RESTART MS` gives the real figure).

- **Match the order:** list assets in the manifest in the order the loader loads them, and load them in that order. A loop over the descriptors (`ldx #FIRST`, then `leax DMPKG_DESC_SIZE,x` each time) keeps the two together.
- **Check the layout:** `dmpkg list --map IMAGE --names REPORT` shows where each asset went, and whether a small one was packed out of order (see [Lessons learned](#placement-can-break-load-order)).
- **Track 17:** on a disk image no asset crosses the directory track, so expect one restart there per load.

### Size assets so they pack

- **Around track 17:** keep disk assets to about 16K, so they can fill the space before track 17 and carry on after it ([why](#a-big-asset-can-blow-up-the-image)).
- **The disk size:** an image under 720 sectors (184,320 bytes) is a normal 35-track disk. Past that it's padded to 737,792 bytes, which costs card space but not time.

### Prefer raw assets

A `decb` asset (a LOADM file) is read record by record: the record headers come through `dmpkg_getbyte`, and only the data goes through the fast copy. Convert a big program to raw `load:` or `phys:` assets, one per segment, as the repacks do. That also makes it easy to keep everything in image order.

### Run the CPU at 1.79 MHz, in 6809 mode

- **Double speed:** on a CoCo 3, `sta $FFD9` (`POKE 65497,0`) before loading. The copy is CPU-bound, so this is the biggest single gain.
- **Not 6309 native mode:** stay in emulation mode (`LDMD #0`) while dmpkg runs ([why](#reading-too-fast-breaks-loads)).

### Call the runtime the cheap way

- **`dmpkg_init` once:** call it at start, then `dmpkg_open` once per image. Each open restarts the stream at the header.
- **One image at a time:** keep it open for the whole load. Opening another image is fine, but costs a restart each time.
- **Keep work between loads short:** while the loader does something else (printing a `LOADING:` line, building tables), the stream just waits. That's safe, but the time adds up.

### Leave the timing settings alone

Don't shorten `DMPKG_DELAY` or make the reads faster without a long run of the [stress test](HWTEST.md#stress-test) at the new settings ([why](#reading-too-fast-breaks-loads)).

## Lessons learned

What the repacks and the hardware taught us, and what was tried and dropped.

### Placement can break load order

Placement is *first fit*: each asset takes the first gap it fits in. A small asset can land in an earlier gap, left by `align: true` or before track 17 on a disk, and then it's out of load order. One 300K repack's disk map showed a handful of small assets packed into tracks 14–15, ahead of where they load, which cost it ten-odd restarts per load. `dmpkg list --map` makes this easy to see.

### A big asset can blow up the image

In one repack, 112K of game data as one asset didn't fit the space before track 17, so it went after it. That pushed the image past 720 sectors, padded it to 737,792 bytes, left 60K before track 17 empty, and put the last chunk first in the image (a restart). As seven 16K assets, they pack before and after track 17: a plain 161,280-byte disk, streamed in order.

### Reading too fast breaks loads

- **6309 native mode:** on real hardware it failed the header check every time, even with `DMPKG_DELAY` at 64. That delay only adds a wait before polling READY, so the data reads themselves seem to outrun the SDC.
- **Occasional errors at full speed:** Rick at 1.79 MHz in emulation mode sometimes shows `LOAD ERROR 4`. It isn't diagnosed yet; reads close to the SDC's limit are the main suspect. The [stress test](HWTEST.md#stress-test) is there to measure how often it happens.
- **So:** the copy already reads about as fast as the SDC seems to take, and a smaller `DMPKG_DELAY` would save 1.5% at most.

### What didn't help

These come up as ways to speed up loading. Each was measured or worked through for dmpkg, and none is worth it.

**Compression.** It pays off when the medium is slow and the CPU waits for it, as with tape or floppy. With the SDC it's the other way round: the CPU is the bottleneck, and it spends about 5.7 cycles on each byte the stream delivers. A load of N bytes compressed to a fraction r of its size takes about r × N × 5.7 cycles to stream, plus N × D to unpack, where D is the decompressor's cycles per output byte. That only beats N × 5.7 if D < (1 − r) × 5.7. At r = 0.5, the decompressor would need under 2.9 cycles per byte, less than a single 6809 load and store, and real LZ-style decompressors take several times the copy's 5.7. Compression could only help if the SDC itself were the limit (a `BLOCK MS` far above the copy's CPU time), and then only by the share of time the card spends filling its buffer. It would also need a buffer to unpack from, which phys loads and the MMU window make awkward.

**Fill blocks** (storing a run of one byte as a length). Only one repack had much: 26K of `$FF` runs in 128K, all at the ends of 16K banks. Skipping them saves about 52 blocks of streaming, but the CPU still has to write those bytes, so the net gain is a few tenths of a second. That isn't worth a new asset type in the format, the packer and the runtime.

**Jumping into the unrolled loop for the tail** (Duff's device). It replaces the loop for the 0–31 leftover bytes of a chunk. Measured on whole loads of two repacks: −0.19% on one and +0.06% on the other, for 13 more bytes of runtime. The leftover bytes are too small a share of the time.

**Stack blasting.** `PULU`/`PULS` read consecutive addresses, but the SDC's data is always at `$FF4A`/`$FF4B`, so the reads can't be blasted. Blasting the writes (`PSHU` of D, X and Y) would save about a quarter of the copy's cycles. But pushes fill memory downwards, so the packer would have to store every asset reversed in 6-byte groups. The three loads back to back would also read the SDC about twice as fast, which is the pattern that already breaks 6309 native mode.

**Direct-page addressing for the SDC registers** (DP = `$FF`). `LDD <$4A` takes the same 5 cycles as the copy loop's indexed `LDU ,Y`, so the copy gains nothing. Every entry point would have to save and restore DP, and a program's interrupt handlers would see the wrong DP during a load.

### What did help

- **CPU speed:** Rick on real hardware loads in about 6 seconds at 0.89 MHz and about 4 at 1.79 MHz.
- **Cutting work out of the copy loop:** X as the destination, U carrying the data, A counting passes in place of a memory counter, and `ABX` to step to the next 32 bytes. Together those took about 3.4% off whole loads (one 300K load went from 4,342,590 to 4,196,826 cycles), and the runtime got a byte smaller.
- **Sizing assets to the disk:** seven 16K assets in place of one 112K one (above).

## Measure before you optimise

- **Hardware test:** `BLOCK MS` is the real time per 512-byte block, `RESTART MS` the extra cost of a restart. If `BLOCK MS` is close to the copy's 1.6 ms of CPU time per block, loads are limited by the CPU, and CPU speed and fewer instructions help. If it's well above, the SDC is the limit, and only fewer restarts help.
- **Stress test:** checks a change didn't make loads less reliable. Run it for a few hundred passes at the speed the loader uses.
- **Emulator:** the repack tests print the cycles a load takes, which is a quick way to compare two versions of a loader or the runtime. They count CPU work, not the SDC's real timing.
