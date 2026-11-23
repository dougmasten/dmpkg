# Tests for a real CoCo SDC

`make test` checks dmpkg against a model of the CoCo SDC. The tests here run on the real machine, so they catch what the model can't. Each has its own folder and Makefile:

| folder | what it does | images |
|---|---|---|
| [main/](main/README.md) | the hardware test: one pass over every case a loader can check by itself (presence and version, STREAM from a mounted disk, mount by name, phys loads, past track 34, timing), with a result per cell | `HWTEST.DSK`, `HWBIG.DSK`, `HWTEST.SDS` |
| [stress/](stress/README.md) | the stress test: streams the same 2M over and over and checks every byte, to catch errors that only turn up now and then | `STRESS.DSK` |

```sh
make            # build and verify both (make hwtest at the top does the same)
make clean
```

You need python3, lwasm, and this repo: each Makefile finds dmpkg two folders up (`DMPKG=` to point elsewhere). Both tests also run against the model as part of `make test` at the top.

The full description, and how to read each screen, is in [docs/HWTEST.md](../docs/HWTEST.md).
