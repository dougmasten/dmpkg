#!/usr/bin/env python3
"""
The packer's disk layout: first fit around track 17, padding past 720
sectors, disk full, disk.reserve and disk.data, the map report's reserve
line, the loader placed on the disk; and every output's folder created.
"""
import sys

from harness import *  # noqa: F401,F403  (the model, the machine, helpers)


LAYOUT_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
        jsr     dmpkg_init          once, at start
        lbcs    fail
        ldx     #DMPKG_MountName
        ldy     #DMPKG_BuildID
        jsr     dmpkg_open
        bcs     fail
        ldx     #GAPFILL
        jsr     dmpkg_load
        bcs     fail
        ldx     #HUGE
        jsr     dmpkg_load
        bcs     fail
        clra
fail    sta     $0100
        jsr     dmpkg_close
        bra     done

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
"""


def disk_layout_test():
    """First fit around track 17 on a synthetic disk. BIG (70,000) fills most
    of tracks 0-16; NEXT (20,000) won't fit before track 17 so it goes to
    track 18; GAPFILL (3,000) then drops into the space left before track 17;
    HUGE (100,000) runs off the end of track 34, which pushes the image past
    720 sectors, so it's padded out of the SDC's double-sided range."""
    rng = random.Random(17)
    sizes = {"big": 70000, "next": 20000, "gapfill": 3000, "huge": 100000}
    results = []
    with tempfile.TemporaryDirectory() as td:
        data = {}
        for n, size in sizes.items():
            data[n] = mkassets.rand_bytes(rng, size)
            with open(os.path.join(td, n + ".bin"), "wb") as f:
                f.write(data[n])
        with open(os.path.join(td, "tiny.bin"), "wb") as f:   # stand-in loader
            f.write(b"\x00\x00\x01\x30\x00\x39\xff\x00\x00\x30\x00")
        assets = [{"name": "big", "file": "big.bin", "phys": 0x20000},
                  {"name": "next", "file": "next.bin", "phys": 0x40000},
                  {"name": "gapfill", "file": "gapfill.bin", "load": 0x6000},
                  {"name": "huge", "file": "huge.bin", "phys": 0x60000}]
        doc = {"project": {"name": "Layout", "image": "L.DSK", "equates": "assets.inc",
                           "table": "assettab.asm", "disk": {"loader": "tiny.bin"}},
               "assets": assets}
        man = os.path.join(td, "p.yaml")
        with open(man, "w") as f:
            yaml.safe_dump(doc, f)
        res = pack.build(man)
        pos = {a.name: a.block * 512 + a.off for a in res["assets"]}
        img = read(res["image"])
        results.append(check(pos == {"BIG": 512, "NEXT": 82944, "GAPFILL": 70512,
                                     "HUGE": 102944},
                             f"first fit around track 17 {pos}"))
        results.append(check(len(img) == decb.HD_MIN,
                             f"image past 720 sectors padded to {decb.HD_MIN} bytes "
                             f"(got {len(img)})"))
        results.append(check(verify(res["image"]), "dmpkg verify passes"))
        files = disk_files(img)
        results.append(check(files["TINY.BIN"][2] == read(os.path.join(td, "tiny.bin"))
                             and "AUTOEXEC.BAS" in files,
                             "loader and AUTOEXEC.BAS fit in the granules left over"))
        t17 = img[decb.DIR_LO:decb.DIR_HI]
        results.append(check(t17[512:512 + 32].startswith(b"DMPKG   DAT"),
                             "track 17 holds the directory, no asset data"))
        prog = lwasm_src(LAYOUT_ASM, td)
        m = Machine({"L.DSK": img}, mounted="L.DSK")
        m.run(prog, 0x1000, 0x1002)
        err = m.peek(0x100, 1)[0]
        results.append(check(err == 0 and m.peek(0x6000, 3000) == data["gapfill"]
                             and bytes(m.phys[0x60000:0x60000 + 100000]) == data["huge"],
                             f"GAPFILL and HUGE (crossing the end of track 34) load "
                             f"(A=${err:02X})"))

        # a disk with no room left for the loader is an error
        doc["assets"] = [{"name": "fill", "file": "fill.bin", "phys": 0x100000}]
        with open(os.path.join(td, "fill.bin"), "wb") as f:
            f.write(bytes(decb.DISK_BYTES - decb.DIR_HI))
        doc["assets"].insert(0, {"name": "low", "file": "low.bin", "phys": 0})
        with open(os.path.join(td, "low.bin"), "wb") as f:
            f.write(bytes(decb.DIR_LO - 512))
        with open(man, "w") as f:
            yaml.safe_dump(doc, f)
        try:
            pack.build(man)
            full = None
        except PackError as e:
            full = str(e)
        results.append(check(full and full.startswith("disk full"),
                             f"no granule left for the loader -> {full!r}"))

        # reserve: 2 keeps the two granules before track 17 for the loader
        # and AUTOEXEC.BAS; LOW (all of tracks 0-16) carries on past track 17
        doc["project"]["disk"]["reserve"] = 2
        with open(man, "w") as f:
            yaml.safe_dump(doc, f)
        res = pack.build(man)
        pos = {a.name: a.block * 512 + a.off for a in res["assets"]}
        files = disk_files(read(res["image"]))
        results.append(check(pos == {"LOW": decb.DIR_HI, "FILL": decb.DIR_HI + decb.DIR_LO - 512}
                             and "TINY.BIN" in files and "AUTOEXEC.BAS" in files,
                             f"reserve: 2 leaves room for the loader {pos}"))
        doc["project"]["map"] = "R.map"
        with open(man, "w") as f:
            yaml.safe_dump(doc, f)
        pack.build(man)
        with open(os.path.join(os.path.dirname(man), "R.map")) as f:
            rline = [ln.strip() for ln in f if ln.startswith("  reserve")]
        del doc["project"]["map"]
        results.append(check(rline == ["reserve 2 granule(s) kept free before track 17 (32-33) "
                                       "for the loader and AUTOEXEC.BAS"],
                             f"the map report shows disk.reserve {rline}"))
        doc["project"]["disk"]["reserve"] = 34
        with open(man, "w") as f:
            yaml.safe_dump(doc, f)
        try:
            pack.build(man)
            big = None
        except PackError as e:
            big = str(e)
        results.append(check(big and big.startswith("project.disk.reserve"),
                             f"reserve past the TOC is an error -> {big!r}"))

        # data: names the file that owns the dmpkg granules
        doc["project"]["disk"]["reserve"] = 2
        doc["project"]["disk"]["data"] = "game.dat"
        with open(man, "w") as f:
            yaml.safe_dump(doc, f)
        res = pack.build(man)
        files = disk_files(read(res["image"]))
        results.append(check(sorted(files) == ["AUTOEXEC.BAS", "GAME.DAT", "TINY.BIN"]
                             and verify(res["image"]),
                             f"data: game.dat names the data file {sorted(files)}, verify passes"))
        for clash in ("tiny.bin", "autoexec.bas"):
            doc["project"]["disk"]["data"] = clash
            with open(man, "w") as f:
                yaml.safe_dump(doc, f)
            try:
                pack.build(man)
                err = None
            except PackError as e:
                err = str(e)
            results.append(check(err and err.startswith("project.disk.data"),
                                 f"data: {clash} clashes -> {err!r}"))
    return all(results)


def output_dirs_test():
    """Every output's folder is created, not just the image's."""
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "a.bin"), "wb") as f:
            f.write(bytes(300))
        outs = {"image": "img/G.SDS", "equates": "inc/deep/assets.inc",
                "table": "tab/assettab.asm", "map": "reports/G.map"}
        with open(os.path.join(td, "p.yaml"), "w") as f:
            yaml.safe_dump({"project": outs,
                            "assets": [{"name": "a", "file": "a.bin", "load": 0x4000}]}, f)
        pack.build(os.path.join(td, "p.yaml"))
        made = [p for p in outs.values() if os.path.isfile(os.path.join(td, p))]
    return check(len(made) == len(outs), f"output folders created for {made}")


def run():
    ok = disk_layout_test()
    ok &= output_dirs_test()
    return ok

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
