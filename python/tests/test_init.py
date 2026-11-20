#!/usr/bin/env python3
"""
dmpkg_init on its own: the SDC found and its version returned, DMPKG_EVER
for firmware older than 117 (dmpkg_ver holding it), and DMPKG_ENODEV with no
SDC, without a command written.
"""
import sys

from harness import *  # noqa: F401,F403  (the model, the machine, helpers)


INIT_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
        jsr     dmpkg_init
        std     $0104               D = version, or A = error code
        tfr     cc,a
        sta     $0100               CC: carry in bit 0
        ldd     dmpkg_ver
        std     $0106
        jsr     dmpkg_close
        bra     done

DMPKG_MAGIC equ $444D504B       no assets.inc here
        include "dmpkg.asm"
"""


def init_test():
    """dmpkg_init: finds the SDC and returns its version in D, DMPKG_EVER if
    it's below 117 (dmpkg_ver holding it, 0 before 113), or DMPKG_ENODEV
    without writing a command when nothing is there."""
    results = []
    with tempfile.TemporaryDirectory() as td:
        prog = lwasm_src(INIT_ASM, td)
    for fw in (0x0129, 0x0117):
        m = Machine({}, firmware=fw)
        m.run(prog, 0x1000, 0x1002)
        cc, d = m.peek(0x100, 1)[0], int.from_bytes(m.peek(0x104, 2), "big")
        results.append(check(not cc & 1 and d == fw and not m.sdc.cmdmode,
                             f"dmpkg_init, firmware {fw:X}: D=${d:04X}, carry {cc & 1}, "
                             "back in emulation mode after dmpkg_close"))
    for fw, ver in ((0x0116, 0x0116), (0x0110, 0)):
        m = Machine({}, firmware=fw)
        m.run(prog, 0x1000, 0x1002)
        cc, a = m.peek(0x100, 1)[0], m.peek(0x104, 1)[0]
        v = int.from_bytes(m.peek(0x106, 2), "big")
        results.append(check(cc & 1 and a == EVER and v == ver,
                             f"dmpkg_init, firmware {fw:X} -> DMPKG_EVER (A=${a:02X}, "
                             f"carry {cc & 1}, dmpkg_ver=${v:04X})"))
    m = Machine({}, present=False)
    c0 = m.cpu.cycles
    m.run(prog, 0x1000, 0x1002)
    cc, a = m.peek(0x100, 1)[0], m.peek(0x104, 1)[0]
    results.append(check(cc & 1 and a == ENODEV and not m.sdc.log,
                         f"dmpkg_init, no SDC -> DMPKG_ENODEV (A=${a:02X}, carry {cc & 1}, "
                         f"{m.cpu.cycles - c0} cycles, no command written)"))
    return all(results)


def run():
    return init_test()

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
