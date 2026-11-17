# dmpkg packer

The Python half of [dmpkg](../README.md): it packs a project's assets into an
image for the CoCo SDC `STREAM` command and writes the lwasm include files
the 6809 runtime (`../asm/dmpkg.asm`) needs.

```sh
pip install -e .            # from this folder; puts `dmpkg` on PATH
dmpkg build project.yaml -v
dmpkg verify IMAGE
dmpkg list   IMAGE
```

`python -m dmpkg` works too. The manifest, the image format and the runtime
are described in [../docs/](../docs/): [MANIFEST.md](../docs/MANIFEST.md),
[RUNTIME.md](../docs/RUNTIME.md) and [FORMAT.md](../docs/FORMAT.md). License: zlib, see [LICENSE](../LICENSE).
