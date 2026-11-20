#!/usr/bin/env python3
"""
The loaders of the tests for a real CoCo SDC (make hwtest builds them), run
against the model first: the hardware test on a CoCo 2 and a CoCo 3 (clean,
a corrupted asset, HWTEST.SDS missing, no field sync), and the stress test
(a clean pass, a corrupted byte, a stale build_id).
"""
import sys

from harness import *  # noqa: F401,F403  (the model, the machine, helpers)


def hwtest_test():
    """The hardware test loaders (make hwtest), run here first: on a CoCo 2
    and a CoCo 3, then with a corrupted asset and with HWTEST.SDS missing."""
    hb = os.path.join(ROOT, "hwtest", "main", "build")
    names = ("HWTEST.DSK", "HWBIG.DSK", "HWTEST.SDS")
    if not all(os.path.isfile(os.path.join(hb, n)) for n in names):
        return check(False, "hwtest: images not built (make hwtest)")
    card = {n: read(os.path.join(hb, n)) for n in names}
    results = []

    def run(disk, coco3, files=None, field=True):
        img = (files or card)[disk]
        segs, exe = decb.parse_loadm(disk_files(img)[disk.replace("DSK", "BIN")][2], disk)
        m = Machine(dict(files or card), mounted=disk)
        if not field:
            m.next_field = float("inf")
        m.mem.load(0xFFFE, b"\x8c\x1b" if coco3 else b"\xa0\x27")
        fails = m.call(segs, exe)
        return fails, screen(m), m

    for disk in ("HWTEST.DSK", "HWBIG.DSK"):
        for coco3 in (False, True):
            fails, text, m = run(disk, coco3)
            what = f"hwtest {disk} on a CoCo {3 if coco3 else 2}"
            cells = " ".join(text)
            results.append(check(fails == 0 and text[-1] == "ALL PASSED"
                                 and "SDC FW     1.27" in cells
                                 and ("F_PHYS     OK" if coco3 else "F_PHYS     SKIP") in cells
                                 and ("TAIL       OK" in cells) == (disk == "HWBIG.DSK")
                                 and m.sdc.mounted == disk,
                                 f"{what}: all passed, disk remounted"))
            # timing: the model's tick counts give 6.4 ms a block and 1.4 ms a
            # restart; both move when the copy loop's speed changes
            tm = re.findall(r"(BLOCK|RESTART) MS +(\S+)", cells)
            results.append(check("BLOCK MS   6.4" in cells and "RESTART MS 1.4" in cells
                                 and m.peek(0x010C, 3) == bytes(3) and not m.crb & 1,
                                 f"{what}: timing cells {tm}, IRQ hook and PIA put back"))
    bad = dict(card)
    img = bytearray(card["HWTEST.DSK"])
    img[3 * 512 + 43 + 700] ^= 0xFF                 # a byte in the middle of C_MULTI
    bad["HWTEST.DSK"] = bytes(img)
    fails, text, _ = run("HWTEST.DSK", False, bad)
    results.append(check(fails == 1 and "C_MULTI    bad" in " ".join(text)
                         and text[-1] == "1 FAILED",
                         "hwtest: a corrupted byte shows C_MULTI BAD (inverse), 1 failed"))
    nosds = {k: v for k, v in card.items() if k != "HWTEST.SDS"}
    fails, text, m = run("HWTEST.DSK", False, nosds)
    results.append(check(fails == 1 and "MOUNT SDS  e3" in " ".join(text)
                         and m.sdc.mounted == "HWTEST.DSK",
                         "hwtest: HWTEST.SDS missing shows MOUNT SDS E3, disk still remounted"))
    fails, text, _ = run("HWTEST.DSK", False, field=False)
    cells = " ".join(text)
    results.append(check(fails == 0 and "BLOCK MS   NOIRQ" in cells
                         and "RESTART MS SKIP" in cells,
                         "hwtest: no field sync IRQ shows BLOCK MS NOIRQ, not a failure"))
    if not all(results):
        print("\n".join("    |" + r for r in text))
    return all(results)


def stress_test():
    """The stress test loader (make hwtest): a clean pass, a byte corrupted
    in one asset (found, with its offset and the bytes read and wanted), and
    a stale build_id (counted as E4 at open). Keys come from a stand-in for
    the ROM's POLCAT, which answers with nothing until the poll it's told."""
    path = os.path.join(ROOT, "hwtest", "stress", "build", "STRESS.DSK")
    if not os.path.isfile(path):
        return check(False, "stress: image not built (make hwtest)")
    img = read(path)
    segs, exe = decb.parse_loadm(disk_files(img)["STRESS.BIN"][2], "STRESS.BIN")
    results = [check(all(a + len(d) <= 0x2600 for a, d in segs),
                     "stress: loader ends below $2600 (AUTOEXEC.BAS)")]

    def run(disk, key_at):
        m = Machine({"STRESS.DSK": disk}, mounted="STRESS.DSK")
        polls = [0]
        rom = {0xA000: 0xA1, 0xA001: 0x00,           # [POLCAT] -> lda $A1F0 / rts
               0xA100: 0xB6, 0xA101: 0xA1, 0xA102: 0xF0, 0xA103: 0x39}

        def rd(_c, _a, addr):
            if addr == 0xA1F0:
                polls[0] += 1
                return 0x20 if polls[0] >= key_at else 0
            return rom.get(addr, 0)
        m.mem.add_read_byte_callback(rd, 0xA000, 0xA1FF)
        m.call(segs, exe, max_ops=60_000_000)
        rows = {r.split("  ")[0].strip(): r for r in screen(m)}
        return m, screen(m), rows

    def num(rows, label):
        return int(rows[label][len(label):].split()[0])

    # clean: 64 asset polls answer nothing, the poll after the pass stops it
    m, text, rows = run(img, 65)
    results.append(check(num(rows, "PASSES") == 1 and num(rows, "BAD SUM") == 0
                         and num(rows, "OPEN ERRORS") == 0 and num(rows, "LOAD ERRORS") == 0
                         and m.sdc.streams_started == 3 and not m.sdc.cmdmode,
                         f"stress: a clean pass of 64 assets ({m.cpu.cycles} cycles): streams for "
                         "the header, the first asset and past track 17 only, closed"))
    # one byte of s05 corrupted on the disk
    _, _, ents = image.read_image(path)
    bad = bytearray(img)
    pos = ents[5]["block"] * 512 + ents[5]["off"] + 0x123
    want = bad[pos]
    bad[pos] ^= 0x5A
    m, text, rows = run(bytes(bad), 65)
    line = " ".join(text)
    results.append(check(num(rows, "PASSES") == 1 and num(rows, "BAD SUM") == 1
                         and "ASSET 05 OFF $0123" in line
                         and f"GOT ${want ^ 0x5A:02X}  WANT ${want:02X}" in line,
                         "stress: a corrupted byte -> BAD SUM 1, ASSET 05 OFF $0123, "
                         "the bytes read and wanted"))
    # stale build_id: every open fails with E4; the first poll stops it
    stale = bytearray(img)
    stale[4] ^= 0xFF
    m, text, rows = run(bytes(stale), 1)
    line = " ".join(text)
    results.append(check(num(rows, "OPEN ERRORS") == 1 and num(rows, "PASSES") == 0
                         and "E4     1" in line and "LAST ERROR  E4 AT OPEN" in line,
                         "stress: a stale build_id -> OPEN ERRORS 1, E4 1, last error E4 at open"))
    return all(results)


def run():
    ok = hwtest_test()
    ok &= stress_test()
    return ok

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
