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
```

`dmpkg extract` writes one asset back out after checking it against its
CRC: a raw asset as stored, a `decb` one as a LOADM file again. NAME is the
asset's name, looked up in the build's map report (IMAGE with `.map`, or
`--names REPORT`), or its TOC index (`3` or `#3`). The default output is
`NAME.bin`; `-o -` writes to stdout.

`python -m dmpkg` works too. The manifest, the image format and the runtime
are described in [../docs/](../docs/): [MANIFEST.md](../docs/MANIFEST.md),
[RUNTIME.md](../docs/RUNTIME.md) and [FORMAT.md](../docs/FORMAT.md). License: zlib, see [LICENSE](../LICENSE).
