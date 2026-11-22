# dmpkg packer

The Python half of [dmpkg](../README.md): it packs a project's assets into an
image for the CoCo SDC `STREAM` command and writes the lwasm include files
the 6809 runtime (`../asm/dmpkg.asm`) needs.

```sh
pip install -e .            # from this folder; puts `dmpkg` on PATH
dmpkg build project.yaml -v
dmpkg verify IMAGE
dmpkg list   IMAGE
dmpkg extract IMAGE NAME [-o OUT]
dmpkg decb dir|copy|free|fstat|list ...
```

`dmpkg extract` writes one asset back out after checking it against its
CRC: a raw asset as stored, a `decb` one as a LOADM file again. NAME is the
asset's name, looked up in the build's map report (IMAGE with `.map`, or
`--names REPORT`), or its TOC index (`3` or `#3`). The default output is
`NAME.bin`; `-o -` writes to stdout.

`dmpkg decb` reads DECB disk images, for repacks that start from a
game's original disks. Its commands and `DISK,FILE` paths are Toolshed's,
read-only, and give the same output:

```sh
dmpkg decb dir   RICK1.DSK,                    # name, type, A/B, granules
dmpkg decb copy  RICK1.DSK,MOVER.BIN out/      # several sources, file or dir
dmpkg decb free  RICK1.DSK
dmpkg decb fstat RICK1.DSK,MOVER.BIN           # plus LOADM segments and exec
dmpkg decb fstat MOVER.BIN                     # a native LOADM file
dmpkg decb list -t RICK1.DSK,RICK.BAS          # -t: detokenize BASIC
```

Scripts can read a disk directly: `dmpkg.decb.read_files(img)` returns
each file's name, type, ASCII flag, bytes and granule chain, and
`dmpkg.decb.parse_loadm(data, name)` a LOADM file's segments and exec
address.

`python -m dmpkg` works too. The manifest, the image format and the runtime
are described in [../docs/](../docs/): [MANIFEST.md](../docs/MANIFEST.md),
[RUNTIME.md](../docs/RUNTIME.md) and [FORMAT.md](../docs/FORMAT.md). License: zlib, see [LICENSE](../LICENSE).
