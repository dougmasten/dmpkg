#!/usr/bin/env python3
"""
A DECB record's length word split across a block that fails: DMPKG_EFAIL
comes back in A, both for the firmware's real behaviour (the stream ends
with status $08) and for the FAILED bit the User Guide describes.
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


def run():
    return decb_word_fail_test()

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
