#!/usr/bin/env python3
"""
The runtime end to end, on the example project with random assets: the
example as a single-file DECB disk (its loader assembled, put on the disk,
read back through the DECB directory and run), the same manifest as a bare
stream image mounted by name, a stale build_id, a second open
while a stream runs, a card that stalls, no SDC, and the firmware gate.

  test_runtime.py        (TEST_SEED=n repeats a run with the seed it printed)
"""
import os
import random
import shutil
import sys
import tempfile

from harness import *  # noqa: F401,F403  (the model, the machine, helpers)



TEST_ASM = r"""
        org     $1000
        bra     start
done    bra     done                fixed at $1002 - harness stops here
start
        jsr     dmpkg_init          once, at start
        lbcs    fail
        ldx     #DMPKG_MountName
        ldy     #{hdr}
        jsr     dmpkg_open
        lbcs    fail
        ldx     #FONT               in image order...
        jsr     dmpkg_load
        lbcs    fail
        ldx     #PALETTE            ...packed behind it, no restart expected
        jsr     dmpkg_load
        lbcs    fail
        ldx     #TITLE              forward seek (aligned), phys over 3 windows
        jsr     dmpkg_load
        lbcs    fail
        ldx     #LEVEL1             backward seek + skip to offset 271
        jsr     dmpkg_load
        lbcs    fail
        ldx     #MAP                packed behind LEVEL1, no restart expected
        jsr     dmpkg_load
        lbcs    fail
        ldx     #CODE               decb, in TITLE's align gap behind MAP: no restart
        jsr     dmpkg_load
        lbcs    fail
        stu     $0102
        ldx     #$6400              wipe PALETTE, then reload it out of order:
        ldb     #16                 restart at its block, skip its odd offset
wipe    clr     ,x+
        decb
        bne     wipe
        ldx     #PALETTE
        jsr     dmpkg_load
        lbcs    fail
        jsr     dmpkg_close
        clra
fail    sta     $0100
        ldd     dmpkg_ver
        std     $0104
        bra     done

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
badid   fcb     $12,$34,$56,$78
"""


def assemble(src_dir, build_dir, hdr_label, defines=(), src=None):
    asm = os.path.join(build_dir, "test.asm")
    with open(asm, "w") as f:
        f.write((src or TEST_ASM).replace("{hdr}", hdr_label))
    out = os.path.join(build_dir, "test.bin")
    lst = os.path.join(build_dir, "test.lst")
    subprocess.run([LWASM, "--6809", "--format=raw", "-o", out, f"--list={lst}",
                    *[f"-D{d}" for d in defines],
                    "-I", os.path.join(ROOT, "asm"), "-I", build_dir, asm], check=True)
    with open(out, "rb") as f:
        return f.read(), 0x1002


REOPEN_ASM = r"""
        org     $1000
        bra     start
done    bra     done                fixed at $1002 - harness stops here
start
        jsr     dmpkg_init          once, at start
        lbcs    fail
        ldx     #DMPKG_MountName
        ldy     #{hdr}
        jsr     dmpkg_open
        bcs     fail
        ldx     #FONT               stream now parked inside a block
        jsr     dmpkg_load
        bcs     fail
        ldx     #$6000              wipe FONT
@wipe   clr     ,x+
        cmpx    #$6000+767
        blo     @wipe
        ldx     #DMPKG_MountName    open again without dmpkg_close
        ldy     #{hdr}
        jsr     dmpkg_open
        bcs     fail
        ldx     #FONT
        jsr     dmpkg_load
        bcs     fail
        jsr     dmpkg_close
        clra
fail    sta     $0100
        bra     done

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
"""


def check_assets(m, src, A, chk, pre=""):
    chk(m.peek(0x6000, 767) == src["font.bin"], pre + "FONT bytes")
    chk(m.peek(0x6400, 16) == src["palette.bin"], pre + "PALETTE bytes (packed, odd offset)")
    chk(m.peek(0x6500, 1500) == src["level1.bin"], pre + "LEVEL1 bytes")
    chk(m.peek(0x7000, 1920) == src["map.bin"], pre + "MAP bytes")
    t = src["title.gfx"]
    chk(bytes(m.phys[0x60001:0x60001 + len(t)]) == t,
        pre + "TITLE in physical RAM across MMU windows")
    chk(m.phys[0x60000] == 0 and m.phys[0x60001 + len(t)] == 0, pre + "TITLE no overrun either side")
    chk(m.mmu == 0x3A, pre + "MMU window restored")
    for addr, data in A["CODE"].segs:
        chk(m.peek(addr, len(data)) == data, pre + f"CODE segment ${addr:04X} ({len(data)} bytes)")


def run_all(ex, src):
    results = []

    def chk(cond, msg):
        results.append(check(cond, msg))

    # ---- single-file disk, built the way example/Makefile does it: pass 1
    # writes the include files, the loader is assembled against them, pass 2
    # puts it on the disk
    man = os.path.join(ex, "project.yaml")
    build = os.path.join(ex, "build")
    note = io.StringIO()
    with contextlib.redirect_stderr(note):        # a loader that's just missing...
        pack.build(man)
    chk("LOADER.BIN not found yet" in note.getvalue(),
        "missing loader: disk built without it, with a note")
    note = io.StringIO()
    with contextlib.redirect_stderr(note):        # ...and pass 1, which expects that
        pack.build(man, loader=False)
    chk(note.getvalue() == "", "pass 1 (--no-loader): built quietly without the loader")
    incs = [os.path.join(build, f) for f in ("assets.inc", "assettab.asm")]
    pass1 = [read(f) for f in incs]
    loader = lwasm(os.path.join(ROOT, "example", "loader.asm"),
                   os.path.join(build, "LOADER.BIN"), build, fmt="decb")
    res = pack.build(man)
    img = read(res["image"])
    A = {a.name: a for a in res["assets"]}
    chk([read(f) for f in incs] == pass1, "pass 2 writes the same include files")
    chk(len(img) == decb.DISK_BYTES, f"disk image is 35 tracks ({len(img)} bytes)")
    chk(verify(res["image"]), "dmpkg verify passes on the disk image")
    files = disk_files(img)
    chk(sorted(files) == ["AUTOEXEC.BAS", "DMPKG.DAT", "LOADER.BIN"],
        f"DECB directory {sorted(files)}")
    chk(files["LOADER.BIN"] == (2, 0x00, loader), "LOADER.BIN reads back intact (ML, binary)")
    chk(files["AUTOEXEC.BAS"] == (0, 0x00, AUTOEXEC_TOK),
        'AUTOEXEC.BAS is 10 LOADM"LOADER.BIN":EXEC, tokenized')
    # dmpkg list --map: track-by-track granule owners and assets
    rep = os.path.join(os.path.dirname(man), "build", "DEMO.map")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli_main(["list", "--map", res["image"], "--names", rep])
    lines = out.getvalue().splitlines()
    row = {int(ln.split()[0]): ln for ln in lines if ln[:5].strip().isdigit()}
    chk(rc == 0 and row[0].split()[1:3] == ["0:DMPKG.DAT", "1:DMPKG.DAT"]
        and "header+TOC, FONT, PALETTE" in row[0] and "directory and FAT" in row[17]
        and any("LOADER.BIN" in r for r in row.values())
        and any("AUTOEXEC.BAS" in r for r in row.values()) and len(row) == 35,
        "dmpkg list --map: 35 tracks, granule owners, assets by name, track 17")
    flat = bytearray(img)
    flat[14] &= ~image.H_DISK & 0xFF                 # header flags: not a disk
    sds = os.path.join(build, "FLAT.SDS")
    with open(sds, "wb") as f:
        f.write(flat)
    with contextlib.redirect_stderr(io.StringIO()):
        chk(cli_main(["list", "--map", sds]) == 1, "dmpkg list --map on a stream image: error")
    other = bytearray(img)
    other[8] = image.FORMAT + 1                      # header format this dmpkg doesn't read
    fmtx = os.path.join(build, "FMTX.DSK")
    with open(fmtx, "wb") as f:
        f.write(other)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = cli_main(["verify", fmtx])
    chk(img[8] == image.FORMAT and rc == 1 and f"format {image.FORMAT + 1}" in err.getvalue(),
        f"header byte 8 is format {image.FORMAT}; another format is rejected")

    # dmpkg extract: every asset back out by name (the map report next to the
    # image gives the names), raw ones as stored, CODE as its LOADM file again
    sources = {"font": "font.bin", "palette": "palette.bin", "level1": "level1.bin",
               "map": "map.bin", "title": "title.gfx", "code": "code.bin"}
    got = {}
    with contextlib.redirect_stdout(io.StringIO()):
        for name, fn in sources.items():
            out = os.path.join(build, f"X_{name}.bin")
            rc = cli_main(["extract", res["image"], name.upper(), "-o", out])
            got[name] = rc == 0 and read(out) == src[fn]
        rc = cli_main(["extract", res["image"], "#4", "-o", os.path.join(build, "X_4.bin")])
    chk(all(got.values()) and rc == 0 and read(os.path.join(build, "X_4.bin")) == src["title.gfx"],
        f"dmpkg extract: every asset by name equals its source file, #4 by index {got}")
    bad = bytearray(img)
    bad[1 * 512 + 100] ^= 0xFF                       # a byte inside FONT
    badp = os.path.join(build, "BADX.DSK")
    with open(badp, "wb") as f:
        f.write(bad)
    out = os.path.join(build, "X_bad.bin")
    if os.path.exists(out):
        os.remove(out)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = cli_main(["extract", badp, "0", "-o", out])
    chk(rc == 1 and "CRC mismatch" in err.getvalue() and not os.path.exists(out),
        "dmpkg extract: a corrupted asset is refused and nothing is written")

    # the loader, read off the disk and run as LOADM would: no mount, every
    # asset streamed from the image it came from
    segs, exe = decb.parse_loadm(files["LOADER.BIN"][2], "LOADER.BIN")
    files_on_card = {"DEMO.DSK": img}
    m = Machine(files_on_card, mounted="DEMO.DSK")
    c0 = m.cpu.cycles
    err = m.call(segs, exe)
    print(f"loader: {m.cpu.cycles - c0} cycles, SDC log {m.sdc.log}")
    chk(err == 0, f"loader from the disk: no error (A=${err:02X})")
    check_assets(m, src, A, chk, "  ")
    starts = [e[1] for e in m.sdc.log if e[0] == "stream"]
    chk(not any(e[0] == "mount" for e in m.sdc.log) and starts == [0, 1, 10],
        f"  no mount; one restart, for aligned TITLE {starts}")
    chk(m.sdc.stream is None and not m.sdc.cmdmode, "  closed, back in emulation mode")

    # ---- run 1: full load, out of image order, on the mounted disk
    prog, done = assemble(ex, build, "DMPKG_BuildID")
    m = Machine(files_on_card, mounted="DEMO.DSK")
    c0 = m.cpu.cycles
    m.run(prog, 0x1000, done)
    print(f"run 1: {m.cpu.cycles - c0} cycles, SDC log {m.sdc.log}")
    chk(m.peek(0x100, 1) == b"\0", f"no error (A=${m.peek(0x100,1)[0]:02X})")
    check_assets(m, src, A, chk)
    chk(m.peek(0x102, 2) == b"\x30\x00", "decb exec returned in U")
    starts = [e for e in m.sdc.log if e[0] == "stream"]
    chk(starts == [("stream", 0), ("stream", 1), ("stream", 10), ("stream", 2), ("stream", 2)],
        f"stream restarts only where needed {starts}")
    chk(m.peek(0x6400, 16) == src["palette.bin"],
        "PALETTE reloaded out of order (seek + skip 255 bytes)")
    chk(m.sdc.stream is None and not m.sdc.cmdmode, "closed, back in emulation mode")

    # ---- run 1b: the same load with the state in the direct page
    # (dmpkg_vars.asm included at $00E0 first; DP is 0 in the test machine)
    dprog, ddone = assemble(ex, build, "DMPKG_BuildID",
                            src='        setdp   $00\n        org     $00E0\n'
                                '        include "dmpkg_vars.asm"\n' + TEST_ASM)
    m = Machine(files_on_card, mounted="DEMO.DSK")
    m.run(dprog, 0x1000, ddone)
    chk(m.peek(0x100, 1) == b"\0", f"state in the direct page: no error (A=${m.peek(0x100,1)[0]:02X})")
    check_assets(m, src, A, chk, "  ")
    chk(m.peek(0x6400, 16) == src["palette.bin"] and m.peek(0x102, 2) == b"\x30\x00",
        "  PALETTE reloaded out of order, decb exec in U")
    chk(len(prog) - len(dprog) >= 17 + 60,
        f"  {len(prog) - len(dprog)} bytes smaller: 17 of state, the rest direct addressing")

    # ---- run 2: stale-image build_id check
    bprog, bdone = assemble(ex, build, "badid")
    m = Machine(files_on_card, mounted="DEMO.DSK")
    m.run(bprog, 0x1000, bdone)
    chk(m.peek(0x100, 1)[0] == EHDR, "wrong build_id -> DMPKG_EHDR")

    # ---- run 2b: the image ends inside TITLE (blocks 10+): the stream runs
    # out, the status drops to $00 (no BUSY, no error bit) -> end of file
    m = Machine({"DEMO.DSK": img[:11 * 512]}, mounted="DEMO.DSK")
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1)[0] == EEOF, f"image ends mid-asset -> DMPKG_EEOF (A=${m.peek(0x100, 1)[0]:02X})")

    # ---- run 2d: dmpkg_open again while a stream is still running (FONT
    # loaded, stream parked part way into a block): the state is rmb and
    # dmpkg_init clears it, so the running stream is found by BUSY and aborted
    rprog, rdone = assemble(ex, build, "DMPKG_BuildID", src=REOPEN_ASM)
    m = Machine(files_on_card, mounted="DEMO.DSK")
    m.run(rprog, 0x1000, rdone)
    rr = (m.peek(0x100, 1)[0], m.peek(0x6000, 767) == src["font.bin"],
          ("abort",) in m.sdc.log, m.sdc.streams_started)
    chk(rr == (0, True, True, 4),                    # open, FONT, open, FONT
        f"dmpkg_open while a stream runs: aborted by BUSY, reopened, FONT loads again {rr}")

    # ---- run 3: card hangs part way through TITLE (phys load): READY never
    # comes, so dmpkg_waitready times out. DMPKG_TIMEOUT=1 (65536 polls) keeps
    # the run short; the MMU window must be put back even on the error.
    tprog, tdone = assemble(ex, build, "DMPKG_BuildID", defines=["DMPKG_TIMEOUT=1"])
    m = Machine(files_on_card, mounted="DEMO.DSK")
    m.sdc.stall_block = A["TITLE"].block + 1
    c0 = m.cpu.cycles
    m.run(tprog, 0x1000, tdone, max_ops=50_000_000)
    err = m.peek(0x100, 1)[0]
    chk(err == ETIME, f"card stalls in TITLE -> DMPKG_ETIME (A=${err:02X}, "
                  f"{m.cpu.cycles - c0} cycles)")
    chk(m.mmu == 0x3A, "  MMU window restored after the error")
    chk(m.sdc.stream is None and ("abort",) in m.sdc.log, "  stream stopped after the error")
    chk(m.peek(0x6000, 767) == src["font.bin"], "  assets before it still loaded")

    # ---- no SDC at all: dmpkg_init stops at the presence check
    m = Machine(files_on_card, present=False)
    m.run(prog, 0x1000, done)
    err = m.peek(0x100, 1)[0]
    chk(err == ENODEV and not m.sdc.log, f"no SDC -> DMPKG_ENODEV, no command written (A=${err:02X})")

    # ---- run 4-6: firmware version gate (117 added STREAM, 113 VERSION)
    for fw, want, what in ((0x0117, 0, "firmware 117 accepted"),
                           (0x0116, EVER, "firmware 116 -> DMPKG_EVER"),
                           (0x0110, EVER, "firmware before VERSION -> DMPKG_EVER")):
        m = Machine(files_on_card, firmware=fw, mounted="DEMO.DSK")
        m.run(prog, 0x1000, done)
        err = m.peek(0x100, 1)[0]
        ver = int.from_bytes(m.peek(0x104, 2), "big")
        expect_ver = fw if fw >= 0x0113 else 0
        chk(err == want and ver == expect_ver,
              f"{what} (A=${err:02X}, dmpkg_ver=${ver:04X})")
        if want:
            chk(not any(e[0] in ("mount", "stream") for e in m.sdc.log),
                  "  no mount or stream attempted")

    # ---- the same project as a bare stream image, mounted by name
    with open(man) as f:
        doc = yaml.safe_load(f)
    proj = doc["project"]
    del proj["disk"]
    proj["image"], proj["sdc_path"] = "build/DEMO.SDS", "GAMES/DEMO.SDS"
    sman = os.path.join(ex, "sds.yaml")
    with open(sman, "w") as f:
        yaml.safe_dump(doc, f)
    sres = pack.build(sman)
    simg = read(sres["image"])
    chk([(a.block, a.off) for a in sres["assets"]] == [(a.block, a.off) for a in res["assets"]]
        and sres["build_id"] == res["build_id"],
        "stream image: same layout and build_id as the disk")
    chk(len(simg) == image.MIN_IMAGE and simg[14] == 0, "stream image: 162 blocks, no disk flag")
    prog, done = assemble(ex, build, "DMPKG_BuildID")
    m = Machine({"GAMES/DEMO.SDS": simg})
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1) == b"\0" and ("mount", "GAMES/DEMO.SDS") in m.sdc.log,
        f"stream image mounted by name, no error (A=${m.peek(0x100,1)[0]:02X})")
    check_assets(m, src, A, chk, "  ")

    m = Machine({"OTHER.SDS": simg})
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1)[0] == EMOUNT, "missing file -> DMPKG_EMOUNT")

    m = Machine({"GAMES/DEMO.SDS": simg[:image.MIN_IMAGE - 512]})
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1)[0] == EMOUNT, "image under 82,944 bytes -> DMPKG_EMOUNT")

    return all(results)


def run(seed=None):
    if seed is None:
        seed = int(os.environ.get("TEST_SEED", random.randrange(1 << 32)))
    print(f"assets: random, seed {seed} (rerun with TEST_SEED={seed})")
    with tempfile.TemporaryDirectory() as ex:
        shutil.copy(os.path.join(ROOT, "example", "project.yaml"), ex)
        src = mkassets.make(os.path.join(ex, "assets"), seed)
        return run_all(ex, src)

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
