# Tests

The tests are in `python/tests/`, one module per area, and `run_test.py` runs them all (`make test` does that):

| file | what it covers |
|---|---|
| `harness.py` | no tests: the CoCo SDC model, the 6809 machine with a CoCo 3 MMU window and a 60 Hz field sync, and helpers (lwasm, a DECB directory reader, the text screen, PASS/FAIL checks) |
| `test_runtime.py` | the runtime end to end on the example project (below) |
| `test_disk.py` | the packer's disk layout: first fit around track 17, padding, disk full, `disk.reserve`, `disk.data`, the map report; output folders |
| `test_init.py` | `dmpkg_init`: presence, version, the firmware gate, no SDC |
| `test_basic.py` | the BASIC tokenizer |
| `test_decb.py` | a DECB length word split across a failing block |
| `test_hwtest.py` | the [hardware and stress test](HWTEST.md) loaders against the model |

Each module also runs on its own, for example `PYTHONPATH=python/src python3 python/tests/test_basic.py`. The repack tests (`repacks/*/test.py`) use `harness.py` too.

The runtime tests assemble the real runtime with lwasm and run it on a 6809 core (`pip install -e 'python[test]'` installs it, MC6809). It builds the example as a single-file disk the way `example/Makefile` does, reads `LOADER.BIN` back out through its own DECB directory reader, and runs it against the already-mounted disk. It also builds the same project as a bare stream image (same layout and `build_id`), mounts it by name, and packs a synthetic disk to check first fit around track 17, the padding past 720 sectors, and the "disk full" error when no granule is left for the loader. It talks to a strict CoCo SDC model and a CoCo 3 MMU. The model raises an error if data is read before READY, if READY isn't given time to settle (it stays stale for 20 cycles after each block), if `$FF4B` is read before `$FF4A`, if a pair is read before the firmware could refill it (the MCU's 10 MHz stream loop, see [SDC_FIRMWARE.md](SDC_FIRMWARE.md#what-dmpkg-needs)), if a register is read within 20µs of a command, if a read passes end of file, if `M:` is given an image under 82,944 bytes, or if a STREAM is issued on firmware older than 117. It also covers the firmware gate: 117 is accepted, while 116 and pre-113 firmware (no VERSION command) both give error 2 and never mount. A further run splits a DECB record's length word across a block that fails, and checks that error 7 comes back in A, both for the firmware's real behaviour (the stream ends with status `$08`) and for the FAILED bit the User Guide describes. Another stalls the card part way through the phys load, so READY never comes: with the runtime assembled at `DMPKG_TIMEOUT=1` to keep it short, it checks for error 6, a restored MMU window and a stopped stream. The test checks every loaded byte against the sources. The model runs at 1.79 MHz, and one more run at 2.4 MHz checks for room to spare. The emulator counts exact 6809 cycles: the MC6809 package's own count adds a cycle per memory access and has a few wrong entries, so the harness corrects it, and the first full load checks every instruction it runs against lwasm's cycle listing.

Every test program calls `dmpkg_init` once before `dmpkg_open`. One run calls `dmpkg_open` a second time while a stream is still running (no `dmpkg_close`, no second `dmpkg_init`), and checks that the stream is found by BUSY and aborted, and that the next load works.

It also checks the BASIC tokenizer: four lines against the bytes a real CoCo saved them as, one case for each of the ROM's tokenizing rules (keywords at the start of a name but not inside one, commands before functions, the Super Extended token ranges, `?`, `'`, `ELSE`, `DATA`, `REM`, quotes, lower case), the program file format and line links, listing back, and the exact `AUTOEXEC.BAS` bytes.

The test doesn't use `example/assets/`. Each run writes fresh random assets of the same sizes to a temp folder, with a new seed, and builds `example/project.yaml` there. It prints the seed, and `TEST_SEED=n` repeats that run. `example/mkassets.py` makes the example's own assets with a fixed seed, so the example image and the worked example in [FORMAT.md](FORMAT.md) come out the same every time.

```sh
make test                       # build + verify the example, assemble the runtime, run the test
make test TEST_SEED=1234        # repeat a run with the seed it printed
make test LWASM=/path/to/lwasm  # if lwasm isn't on PATH
```

Other targets: `make hwtest` (the [hardware test](HWTEST.md) disks and the [stress test](HWTEST.md#stress-test) disk; `make test` builds them and runs their loaders against the model), `make asm` (assembles the runtime for 6809 and 6309 at `ASM_ORG`, default `0x2000`, and prints its size), `make clean`. The example builds with its own Makefile: `cd example && make` (also `make verify`, `make list`); `make test` runs it first.

## Not yet verified on hardware

[HWTEST.md](HWTEST.md) has test disks that check most of these on a real SDC.

- The protocol comes from the CoCo SDC guide (as quoted in nowhereman999's `SDC_Stream_File_Library.asm` and VCC's stream emulation). The tests run against a model of the SDC, not a real one. You need STREAM-capable firmware (117 or later). `dmpkg_open` checks this.
- Drive 1 (`DMPKG_DRIVE equ 1` → `$E1`/`$91`) follows the command-bit pattern but hasn't been confirmed.
- The presence check (`$FF42`/`$FF43`) is taken from the FUZIX driver and modelled in the test, not tried on a real SDC. On a machine with a regular floppy controller instead, `$FF42` is likely a mirror of the drive latch at `$FF40`, so the check briefly selects drives (motor off) before saying no SDC.
- `dmpkg_stop` waits for BUSY to clear after `$D0`. The guide says BUSY stays set until EOF or abort; the old library polled READY there instead.
- 6309 native mode: on real hardware a native-mode load failed its build_id check (`DMPKG_EHDR`) every time. The firmware's timing explains it: the header was read with back-to-back `LDD`s, 5 cycles apart in native mode, faster than the MCU refills the data registers. The header reads are now spaced out, and every other read path has room to spare by the firmware's numbers, but native mode hasn't been tried on hardware since. Until it has, switch to emulation mode (`LDMD #0`) before calling dmpkg.
- Disk images: toolshed's `decb` reads the example disk (directory, free space, `LOADER.BIN`, `AUTOEXEC.BAS`), but STREAM on a disk that SDC-DOS mounted with `DRIVE`, `AUTOEXEC.BAS` running the loader, and the hard-disk-size padding past 720 sectors all come from the guide and the firmware disassembly, not from a real SDC.
