#!/usr/bin/env python3
"""
A DECB record's length word split across a block that fails: DMPKG_EFAIL
comes back in A, both for the firmware's real behaviour (the stream ends
with status $08) and for the FAILED bit the User Guide describes. And
dmpkg decb: reading files back off a DECB disk, and its commands.
"""
import sys

from harness import *  # noqa: F401,F403  (the model, the machine, helpers)


FAIL_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
        jsr     dmpkg_init          once, at start
        lbcs    fail
        ldx     #DMPKG_MountName
        ldy     #DMPKG_BuildID
        jsr     dmpkg_open
        lbcs    fail
        ldx     #CODE               length word straddles blocks 1/2
        jsr     dmpkg_load
        bcs     fail
        clra
fail    sta     $0100
        bra     done

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
"""


def decb_word_fail_test():
    """A decb asset packed at offset 511: its first length word is split
    across blocks 1 and 2, and block 2 fails. dmpkg_getword must return the
    error code (DMPKG_EFAIL), not the length's first byte."""
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "pad.bin"), "wb") as f:
            f.write(bytes(511))
        seg = bytes(range(0x40))
        with open(os.path.join(td, "code.bin"), "wb") as f:
            f.write(b"\x00" + len(seg).to_bytes(2, "big") + b"\x30\x00" + seg
                    + b"\xff\x00\x00\x30\x00")
        with open(os.path.join(td, "p.yaml"), "w") as f:
            f.write("project:\n  title: Fail\n  image: F.SDS\n  sdc_path: F.SDS\n"
                    "  equates: assets.inc\n  table: assettab.asm\n"
                    "assets:\n  - {name: pad, file: pad.bin, load: 0x6000}\n"
                    "  - {name: code, file: code.bin, type: decb}\n")
        res = pack.build(os.path.join(td, "p.yaml"))
        code = {a.name: a for a in res["assets"]}["CODE"]
        assert (code.block, code.off) == (1, 511), (code.block, code.off)
        with open(res["image"], "rb") as f:
            img = f.read()
        asm = os.path.join(td, "t.asm")
        with open(asm, "w") as f:
            f.write(FAIL_ASM)
        out = os.path.join(td, "t.bin")
        subprocess.run([LWASM, "--6809", "--format=raw", "-o", out,
                        "-I", os.path.join(ROOT, "asm"), "-I", td, asm], check=True)
        with open(out, "rb") as f:
            prog = f.read()
    results = []
    for status, how in ((0x08, "stream ends with $08"), (0x81, "FAILED bit")):
        m = Machine({"F.SDS": img})
        m.sdc.fail_block = 2
        m.sdc.fail_status = status
        m.run(prog, 0x1000, 0x1002)
        err = m.peek(0x100, 1)[0]
        results.append(check(err == EFAIL, f"decb word split across a failing block ({how}) "
                                       f"-> DMPKG_EFAIL (A=${err:02X})"))
        results.append(check(m.sdc.stream is None and ("abort",) in m.sdc.log,
                             "  stream stopped after the error"))
    return all(results)


def decb_disk_test():
    """dmpkg decb: files of awkward sizes written with write_disk come back
    unchanged through read_files and decb copy; dir, free, fstat and list
    work, and match Toolshed's decb when it's installed."""
    from dmpkg import basic, decb
    from dmpkg.cli import main as cli_main
    seg = bytes(range(256)) * 2
    loadm_file = (b"\x00" + len(seg).to_bytes(2, "big") + b"\x30\x00" + seg
                  + b"\x00\x00\x03\x40\x00" + b"\x01\x02\x03" + b"\xff\x00\x00\x30\x00")
    prog = basic.tokenize('10 PRINT "HELLO"\n20 GOTO 10\n')
    files = [("ONE", "DAT", b"\x41"), ("SECT", "DAT", bytes(256)), ("SECT1", "DAT", bytes(257)),
             ("GRAN", "DAT", bytes(range(256)) * 9), ("GRAN1", "DAT", bytes(2305)),
             ("BIG", "DAT", bytes(9000)), ("CODE", "BIN", loadm_file), ("PROG", "BAS", prog)]
    img = bytearray(decb.DISK_BYTES)
    decb.write_disk(img, [(0, 1)], files)
    results = []
    got = {f["name"]: f for f in decb.read_files(bytes(img))}
    results.append(check(all(got[f"{n}.{e}"]["data"] == d for n, e, d in files),
                         "decb.read_files: every file back unchanged (1 byte to 4 granules)"))
    with tempfile.TemporaryDirectory() as td:
        disk = os.path.join(td, "T.DSK")
        with open(disk, "wb") as f:
            f.write(img)

        def run(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["decb", *args])
            return rc, out.getvalue()
        rc, d = run("dir", disk + ",")
        rows = [ln.split() for ln in d.splitlines()[2:]]
        results.append(check(rc == 0 and ["CODE", "BIN", "2", "B", "1"] in rows
                             and ["BIG", "DAT", "2", "B", "4"] in rows and len(rows) == 9,
                             "decb dir: one row per file (with DMPKG.DAT), type, B, granules"))
        rc, d = run("copy", *[f"{disk},{n.lower()}.{e.lower()}" for n, e, _ in files],
                    os.path.join(td, "out"))
        same = all(read(os.path.join(td, "out", f"{n}.{e}")) == d for n, e, d in files)
        results.append(check(rc == 0 and same, "decb copy: several files to a directory, unchanged"))
        rc, d = run("fstat", f"{disk},CODE.BIN")
        results.append(check(rc == 0 and "Segment            : $3000-$31FF (512 bytes)" in d
                             and "Segment            : $4000-$4002 (3 bytes)" in d
                             and "Exec address       : $3000" in d,
                             "decb fstat: a LOADM file's segments and exec address"))
        rc, d = run("list", "-t", f"{disk},PROG.BAS")
        results.append(check(rc == 0 and d == '10 PRINT "HELLO"\n20 GOTO 10\n',
                             "decb list -t: the BASIC program as text"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, _ = run("copy", f"{disk},NOPE.BIN", os.path.join(td, "x"))
        results.append(check(rc == 1 and "no NOPE.BIN" in err.getvalue(),
                             "decb copy: a missing file is an error"))
        if shutil.which("decb"):
            same = []
            for args in (["dir", disk + ","], ["free", disk], ["fstat", f"{disk},BIG.DAT"],
                         ["list", "-t", f"{disk},PROG.BAS"]):
                ts = subprocess.run(["decb", *args], capture_output=True, text=True).stdout
                same.append(run(*args)[1] == ts)
            results.append(check(all(same), f"decb dir, free, fstat, list -t: same output as "
                                            f"Toolshed's decb {same}"))
    return all(results)


def run():
    return all([decb_word_fail_test(), decb_disk_test()])

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
