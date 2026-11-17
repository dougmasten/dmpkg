# dmpkg — project disc packer for the CoCo SDC STREAM protocol

`dmpkg` packs all of a project's assets into a single image on the SD card. A small 6809 runtime (`asm/dmpkg.asm`, 963 bytes) then loads any asset straight to its destination using the CoCo SDC `STREAM` command. That includes CoCo 3 physical RAM through an MMU window.

The image can also be a DECB disk that holds your loader program, so the whole project is one `.DSK` file: mount it, run the loader (or let `AUTOEXEC.BAS` do it), and the loader streams the assets from that same file. See [docs/DISK.md](docs/DISK.md).

```
project.yaml ──► dmpkg build ───► build/DEMO.DSK      → SD card (or a bare .SDS)
                                  ├─► build/assets.inc     EQUs (no bytes)
                                  ├─► build/assettab.asm   mount name, build_id, descriptors
                                  └─► build/DEMO.map       layout report
```

## Quick start

```sh
pip install -e python              # dmpkg command (or: PYTHONPATH=python/src python3 -m dmpkg)
make build                          # assets, include files, loader, then DEMO.DSK
dmpkg verify example/build/DEMO.DSK
dmpkg list   example/build/DEMO.DSK
```

`make build` runs the steps of a [two-pass build](docs/DISK.md#two-pass-build): `example/mkassets.py` writes the example's assets (random filler, fixed seed), `dmpkg build` writes the include files, lwasm assembles `example/loader.asm` against them, and a second `dmpkg build` puts the loader on the disk. The Makefile runs the package straight from `python/src/`, so it works without installing.

```asm
        ldx     #SDC_MountName      ; from assettab.asm (0 on a disk image: no mount)
        ldy     #SDC_BuildID        ; 0 to skip the stale-image check
        jsr     sdc_open
        bcs     error               ; A = error code
        ldx     #TITLE              ; asset descriptor
        jsr     sdc_load
        bcs     error
        ldx     #CODE               ; DECB asset
        jsr     sdc_load            ; U = exec address
        jsr     sdc_close           ; back to floppy emulation
        ...
        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
```

Requires Python 3.8+ and PyYAML (installed by `pip install -e python`).

```
python/         the packer, a Python project of its own
  pyproject.toml
  src/dmpkg/    pack.py      build(): manifest -> layout -> image -> disk -> outputs
                manifest.py  reading and checking project.yaml
                layout.py    first-fit placement and image size
                image.py     header, TOC, descriptors, build_id, reading images
                decb.py      LOADM files, 8.3 names, the DECB disk
                outputs.py   equates, table and map text
                cli.py       the dmpkg command (build, list, verify)
  tests/        end-to-end test of the packer and runtime (run_test.py)
asm/            the 6809 runtime (dmpkg.asm)
example/        example project, asset generator and loader
docs/           the documentation (below)
Makefile        builds the example and runs the tests across both parts
```

## Documentation

| file | what's in it |
|---|---|
| [docs/MANIFEST.md](docs/MANIFEST.md) | the project manifest: every key, default and check, layout, generated symbols |
| [docs/RUNTIME.md](docs/RUNTIME.md) | the 6809 runtime: calls, error codes, presence and firmware checks, phys loads, configuration |
| [docs/DISK.md](docs/DISK.md) | single-file disk images: the two-pass build, interrupts |
| [docs/FORMAT.md](docs/FORMAT.md) | the image format, byte by byte, with a worked example |
| [docs/TESTING.md](docs/TESTING.md) | the test suite, and what hasn't been tried on real hardware |

```sh
make test                       # build + verify the example, assemble the runtime, run the test
```

## License

zlib; see [LICENSE](LICENSE). Programs that assemble in the runtime don't need to include the notice; it only has to stay in copies of the source.
