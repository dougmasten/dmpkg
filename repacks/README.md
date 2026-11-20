# Game repacks

Commercial CoCo games rebuilt as one dmpkg disk image each: the original disks go in, a single `.DSK` comes out, and its loader streams the whole game from the CoCo SDC with no disk swapping.

Each folder builds from the game's original disks in its `bin/`. Those disks, and everything extracted or built from them, are git-ignored: only the scripts and loader are the repack.

| folder | game | status |
|---|---|---|
| [rick_dangerous/](rick_dangerous/README.md) | Rick Dangerous (CoCo 3, 2 MB, 6309) | works on real hardware |

To build one: `cd repacks/<game> && make all`. Each Makefile finds dmpkg at `../..` (override with `DMPKG=`).
