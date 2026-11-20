#!/usr/bin/env python3
"""
End-to-end test: assemble the real runtime with lwasm, run it on a 6809 core
against a strict CoCo SDC model and a CoCo 3 MMU, compare loaded bytes with
the source assets.

The SDC model enforces the protocol as documented and raises on misuse:
  - data read while READY is low, or $FF4B read before its $FF4A partner
  - READY stays stale for READY_LAG cycles after each block (needs the settle delay)
  - M: refuses an image under 82,944 bytes or not a multiple of 256 (not a DSK)
  - STREAM on an image that isn't 512-aligned is an error
  - BUSY drops at end of file; $D0 or another command ends the stream
  - any register read within CMD_SETTLE cycles of a command write (20us rule)
  - fail_block: the stream fails at that block, with fail_status: $08 and
    BUSY clear (what the firmware does), or $81 (FAILED, as the guide says)
  - stall_block: once the stream reaches that block it stays BUSY and never
    READY (a hung card), so the runtime must time out
  - VERSION ($C0 'V') answers in BCD, or FAILS on firmware before 113;
    STREAM is rejected on firmware before 117
  - mounted=: an image already in drive 0, as when the loader came off it
  - Flash registers: bits 5-6 of the $FF42 data register read back in
    $FF43 (what dmpkg_init's presence check relies on)
  - present=False: no SDC at all; its registers read $FF and any command
    written is logged as an error

The machine has a 60 Hz field sync: PIA0 sets the $FF03 flag every
FIELD cycles, and with $FF03 bit 0 set and IRQ unmasked the CPU takes an
IRQ through $FFF8, which points straight at BASIC's RAM hook $010C.
Reading $FF02 clears the flag.

The example builds as a single-file DECB disk: its loader is assembled and
put on the disk, then read back through the DECB directory and run. The
same manifest without disk: covers the bare stream image and the mount
path. A synthetic disk checks first-fit packing around track 17.
"""
import contextlib
import io
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile

import yaml

logging.disable(logging.CRITICAL)
from MC6809.components.cpu6809 import CPU
from MC6809.components.memory import Memory
from MC6809.tests.test_config import TestCfg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))      # repo root: python/tests -> .
try:
    from dmpkg import basic, decb, image, pack  # noqa: E402
    from dmpkg.cli import cmd_verify  # noqa: E402
    from dmpkg.errors import PackError  # noqa: E402
except ImportError:
    sys.exit("run_test: can't import dmpkg: pip install -e python or set "
             "PYTHONPATH=python/src (make test does)")
sys.path.insert(0, os.path.join(ROOT, "example"))
import mkassets  # noqa: E402

LWASM = os.environ.get("LWASM", "lwasm")

# runtime error codes (DMPKG_E* in asm/dmpkg.asm)
ENODEV, EVER, EMOUNT, EHDR, EEOF, ETIME, EFAIL = range(1, 8)
READY_LAG = 20          # cycles READY stays stale after a block is drained
FILL_TIME = 400         # cycles to "read" the next block from the card
CMD_SETTLE = 36         # 20us at 1.79MHz: no register access after a command
FIELD = 14915           # cycles per 60 Hz field at 0.89MHz


class SDCError(Exception):
    pass


class MockSDC:
    def __init__(self, cpu, files, firmware=0x0127, mounted=None, present=True):
        self.cpu, self.files = cpu, files
        self.present = present        # False: nothing answers at $FF40-$FF4B
        self.fdata = 0                # Flash data register ($FF42)
        self.bank = 0                 # Flash bank (bits 0-2 of $FF43)
        self.firmware = firmware      # BCD, e.g. 0x0127
        self.version_reply = None     # bytes readable at $FF4A/$FF4B
        self.cmd_at = -CMD_SETTLE
        self.fail_block = None        # block number whose read fails
        self.fail_status = 0x08       # firmware: stream ends, $08, no FAILED bit
        self.stall_block = None       # block where the stream hangs (BUSY, never READY)
        self.cmdmode = False
        self.params = [0, 0, 0]
        self.status = 0
        self.mounted = mounted        # image already in drive 0
        self.collect = None           # bytes being written for $E0
        self.stream = None            # dict while streaming
        self.busy_until = 0
        self.ready_at = 0
        self.stale_until = 0
        self.log = []
        self.streams_started = 0

    @property
    def now(self):
        return self.cpu.cycles

    # -- status
    # -- Flash registers
    def write_fdata(self, _c, _a, _addr, v):
        self.fdata = v
        return v

    def read_fctl(self, *_):
        if not self.present:
            return 0xFF
        return self.bank | (self.fdata & 0x60)

    def settle_check(self, addr):
        if self.now - self.cmd_at < CMD_SETTLE:
            raise SDCError(f"read ${addr:04X} {self.now - self.cmd_at} cycles after a command")

    def read_stat(self, *_):
        if not self.present:
            return 0xFF                # empty bus: looks BUSY forever
        self.settle_check(0xFF48)
        st = self.status
        s = self.stream
        if s is not None:
            if self.now < self.stale_until:
                return 0x03            # stale: still claims READY
            if s["eof"]:
                return 0x00
            if s["blk"] == self.fail_block:
                return self.fail_status
            if s["blk"] == self.stall_block:
                return 0x01            # BUSY forever
            return 0x03 if self.now >= self.ready_at else 0x01
        if self.now < self.busy_until:
            return 0x01
        return st

    def write_latch(self, _c, _a, _addr, v):
        if v == 0x43:
            self.cmdmode = True
        elif v == 0:
            self.cmdmode = False
            self.stream = None
        else:
            raise SDCError(f"unexpected latch value ${v:02X}")
        return v

    def write_cmd(self, _c, _a, _addr, v):
        if not self.present:
            self.log.append(("command with no SDC", v))
            return v
        if not self.cmdmode:
            raise SDCError(f"command ${v:02X} outside command mode")
        if self.now < self.busy_until:
            raise SDCError(f"command ${v:02X} while BUSY")
        self.stream = None             # any command ends a stream
        self.version_reply = None
        self.cmd_at = self.now
        if v == 0xC0 and self.params[0] == ord("V"):
            self.busy_until = self.now + 50
            if self.firmware < 0x0113:
                self.status = 0x80     # VERSION not implemented
            else:
                self.status = 0x00
                self.version_reply = [self.firmware >> 8, self.firmware & 0xFF]
            self.log.append(("version",))
        elif v in (0xE0, 0xE1):
            self.collect = bytearray()
            self.status = 0x02         # READY for the 256-byte name block
        elif v in (0x90, 0x91):
            if self.firmware < 0x0117:
                raise SDCError("STREAM on firmware without it")
            if v != 0x90:
                raise SDCError("test mounts drive 0")
            img = self.files.get(self.mounted)
            if img is None:
                raise SDCError("STREAM with nothing mounted")
            if len(img) % 512:
                raise SDCError("STREAM on an image that isn't 512-aligned")
            blk = (self.params[0] << 16) | (self.params[1] << 8) | self.params[2]
            if blk * 512 >= len(img):
                raise SDCError(f"STREAM start block {blk} past end")
            self.stream = dict(img=img, blk=blk, pos=0, eof=False, expect_b=False)
            self.ready_at = self.now + FILL_TIME
            self.stale_until = 0
            self.streams_started += 1
            self.log.append(("stream", blk))
        elif v == 0xD0:
            self.status = 0
            self.busy_until = self.now + 30
            self.log.append(("abort",))
        else:
            raise SDCError(f"unknown command ${v:02X}")
        return v

    def write_param(self, _c, _a, addr, v):
        if self.collect is not None and addr in (0xFF4A, 0xFF4B):
            want = 0xFF4A if len(self.collect) % 2 == 0 else 0xFF4B
            if addr != want:
                raise SDCError("mount block written out of order")
            self.collect.append(v)
            if len(self.collect) == 256:
                name = bytes(self.collect).split(b"\0", 1)[0].decode()
                if any(self.collect[len(name):]):
                    raise SDCError("mount block padding not zero")
                self.collect = None
                if not name.startswith("M:"):
                    raise SDCError(f"mount string {name!r} lacks M:")
                name = name[2:]
                self.mounted = name
                self.busy_until = self.now + 2000
                img = self.files.get(name)
                dsk = img is not None and len(img) >= image.MIN_IMAGE and len(img) % 256 == 0
                self.status = 0x00 if dsk else 0x80   # not found, or too small for a DSK
                self.log.append(("mount", name))
            return v
        if self.stream is not None and addr in (0xFF4A, 0xFF4B):
            raise SDCError("write to data port during stream")
        self.params[addr - 0xFF49] = v
        return v

    def read_data(self, _c, _a, addr):
        if not self.present:
            return 0xFF
        self.settle_check(addr)
        s = self.stream
        if s is None and self.version_reply is not None:
            if self.now < self.busy_until:
                raise SDCError("version read while BUSY")
            return self.version_reply[addr - 0xFF4A]
        if s is None:
            raise SDCError(f"read ${addr:04X} with no stream")
        if s["eof"]:
            raise SDCError("read past end of file")
        if self.now < self.ready_at or self.now < self.stale_until:
            raise SDCError(f"read ${addr:04X} before READY (pos {s['pos']} blk {s['blk']})")
        want = 0xFF4B if s["expect_b"] else 0xFF4A
        if addr != want:
            raise SDCError(f"read ${addr:04X}, expected ${want:04X}")
        s["expect_b"] = not s["expect_b"]
        b = s["img"][s["blk"] * 512 + s["pos"]]
        s["pos"] += 1
        if s["pos"] == 512:
            s["pos"] = 0
            s["blk"] += 1
            if s["blk"] * 512 >= len(s["img"]):
                s["eof"] = True
            self.stale_until = self.now + READY_LAG
            self.ready_at = self.now + FILL_TIME
        return b


class Machine:
    def __init__(self, files, firmware=0x0127, mounted=None, present=True):
        cfg = TestCfg({"verbosity": None, "display_cycle": False, "trace": None,
                       "bus_socket_host": None, "bus_socket_port": None, "ram": None,
                       "rom": None, "max_ops": None, "use_bus": False})
        self.mem = Memory(cfg)
        self.cpu = CPU(self.mem, cfg)
        self.sdc = MockSDC(self.cpu, files, firmware, mounted, present)
        self.phys = bytearray(0x200000)
        self.mmu = 0x3A                       # arbitrary home block for $4000
        m = self.mem
        m.add_write_byte_callback(self.sdc.write_latch, 0xFF40)
        m.add_write_byte_callback(self.sdc.write_fdata, 0xFF42)
        m.add_read_byte_callback(self.sdc.read_fctl, 0xFF43)
        m.add_write_byte_callback(self.sdc.write_cmd, 0xFF48)
        m.add_read_byte_callback(self.sdc.read_stat, 0xFF48)
        m.add_write_byte_callback(self.sdc.write_param, 0xFF49, 0xFF4B)
        m.add_read_byte_callback(self.sdc.read_data, 0xFF4A, 0xFF4B)
        m.add_read_byte_callback(lambda *_: self.mmu, 0xFFA2)
        m.add_write_byte_callback(self._mmu_w, 0xFFA2)
        m.add_read_byte_callback(self._win_r, 0x4000, 0x5FFF)
        m.add_write_byte_callback(self._win_w, 0x4000, 0x5FFF)
        self.crb = 0x34                       # PIA0 CRB: field sync IRQ off
        self.fs = False                       # field sync flag
        self.next_field = FIELD
        m.add_read_byte_callback(self._pia_bd, 0xFF02)
        m.add_read_byte_callback(lambda *_: self.crb | (0x80 if self.fs else 0), 0xFF03)
        m.add_write_byte_callback(self._pia_bc, 0xFF03)
        m.load(0xFFF8, b"\x01\x0c")            # IRQ -> $010C

    def _pia_bd(self, *_):
        self.fs = False
        return 0xFF

    def _pia_bc(self, _c, _a, _addr, v):
        self.crb = v & 0x3F
        return v

    def step(self):
        """One instruction, after the field sync and an IRQ if one is due."""
        c = self.cpu
        if c.cycles >= self.next_field:
            self.next_field += FIELD
            self.fs = True
        if self.fs and self.crb & 1 and not c.get_cc_value() & 0x10:
            c.set_cc(c.get_cc_value() | 0x80)  # E: whole state
            c.push_irq_registers()
            c.set_cc(c.get_cc_value() | 0x10)  # I
            c.cycles += 18
            c.program_counter.set(self.mem.read_word(0xFFF8))
        c.get_and_call_next_op()

    def _mmu_w(self, _c, _a, _addr, v):
        self.mmu = v
        return v

    def _win_r(self, _c, _a, addr):
        return self.phys[(self.mmu << 13) | (addr - 0x4000)]

    def _win_w(self, _c, _a, addr, v):
        self.phys[(self.mmu << 13) | (addr - 0x4000)] = v
        return v

    def run(self, image: bytes, org: int, done: int, max_ops=20_000_000):
        self.mem.load(org, image)
        self.cpu.program_counter.set(org)
        self.cpu.system_stack_pointer.set(0x0F00)
        for _ in range(max_ops):
            if self.cpu.program_counter.value == done:
                return
            self.step()
        raise RuntimeError("program did not finish")

    def call(self, segs, entry, max_ops=20_000_000):
        """LOADM-style: load (addr, data) segments, jsr entry, stop at its rts."""
        for addr, data in segs:
            self.mem.load(addr, data)
        ret = 0x0F80
        self.mem.load(ret, b"\x20\xfe")              # bra * (never reached)
        self.cpu.system_stack_pointer.set(0x0EFE)
        self.mem.load(0x0EFE, ret.to_bytes(2, "big"))
        self.cpu.program_counter.set(entry)
        for _ in range(max_ops):
            if self.cpu.program_counter.value == ret:
                return self.cpu.accu_a.value
            self.step()
        raise RuntimeError("program did not return")

    def peek(self, a, n):
        return bytes(self.mem.read_byte(a + i) for i in range(n))


TEST_ASM = r"""
        org     $1000
        bra     start
done    bra     done                fixed at $1002 - harness stops here
start
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


def lwasm(src, out, inc_dir, fmt="raw", defines=()):
    subprocess.run([LWASM, "--6809", f"--format={fmt}", "-o", out,
                    *[f"-D{d}" for d in defines],
                    "-I", os.path.join(ROOT, "asm"), "-I", inc_dir, src], check=True)
    with open(out, "rb") as f:
        return f.read()


def disk_files(img):
    """Independent DECB reader: {name.ext: (type, ascii, bytes)} via the
    directory (track 17 sectors 3-11) and the FAT (sector 2)."""
    t17 = 17 * 18 * 256
    fat = img[t17 + 256:t17 + 256 + 68]
    out = {}
    for d in range(t17 + 512, t17 + 11 * 256, 32):
        e = img[d:d + 32]
        if e[0] in (0x00, 0xFF):
            if e[0] == 0xFF:
                break
            continue
        data, g = b"", e[13]
        while True:
            trk = g // 2 + (g // 2 >= 17)
            off = trk * 18 * 256 + (g % 2) * 9 * 256
            if fat[g] >= 0xC0:
                n = (fat[g] - 0xC1) * 256 + int.from_bytes(e[14:16], "big")
                data += img[off:off + n]
                break
            data += img[off:off + 9 * 256]
            g = fat[g]
        name = e[0:8].decode().rstrip() + "." + e[8:11].decode().rstrip()
        out[name] = (e[11], e[12], data)
    return out


def assemble(src_dir, build_dir, hdr_label, defines=()):
    asm = os.path.join(build_dir, "test.asm")
    with open(asm, "w") as f:
        f.write(TEST_ASM.replace("{hdr}", hdr_label))
    out = os.path.join(build_dir, "test.bin")
    lst = os.path.join(build_dir, "test.lst")
    subprocess.run([LWASM, "--6809", "--format=raw", "-o", out, f"--list={lst}",
                    *[f"-D{d}" for d in defines],
                    "-I", os.path.join(ROOT, "asm"), "-I", build_dir, asm], check=True)
    with open(out, "rb") as f:
        return f.read(), 0x1002


# 10 LOADM"LOADER.BIN":EXEC as the ROM tokenizes it: LOAD ($D3) then M, the
# name as typed, EXEC ($A2); one line linked for $2601, then $00 $00
AUTOEXEC_TOK = (b"\xff\x00\x17" + b"\x26\x16\x00\x0a" + b"\xd3M" + b'"LOADER.BIN"'
                + b":\xa2\x00" + b"\x00\x00")

# Lines from a tokenized program saved on a real CoCo (ROBOCOP.BAS): text as
# LIST shows it, then the bytes on the disk
BASIC_REAL = [
    ('LINEINPUT"START AT WHICH LEVEL? (1-6) ";A$:IF A$="" THEN 1810',
     "bb89225354415254204154205748494348204c4556454c3f2028312d362920223b41243a85204124"
     "b3222220a72031383130"),
    ("POKE &H42EC,VV:'**POKE THE STARTING LEVEL",
     "92202648343245432c56563a3a832a2a504f4b4520544845205354415254494e47204c4556454c"),
    ("FORX=1TO8:READA:POKEA,&HBD:POKEA+1,&H5E:POKEA+2,&HB3:NEXT",
     "8058b331a5383a8d413a92412c264842443a9241ab312c264835453a9241ab322c264842333a8b"),
    ("POKE&H45C9,&HDF:POKE&H4B3D,&HDF:POKE&H7B03,&HDF:POKE&H4037,&H3F:POKE&H4038,&HBE",
     "922648343543392c264844463a922648344233442c264844463a922648374230332c264844463a92"
     "2648343033372c264833463a922648343033382c26484245"),
]
# The ROM's rules, one case each (bytes worked out from the crunch routine)
BASIC_RULES = [
    ("FORT=1TO10", "8054b331a53130", "keyword at the start of a name: FOR T"),
    ("SCORE=ATOM", "53434f5245b341544f4d", "inside a name: no keywords (no OR, no TO)"),
    ('?"A";:\'X PRINT', "872241223b3a3a8358205052494e54", "? is PRINT, ' is :REM, rest as typed"),
    ('DATA 1,FOR,"A:B",X:PRINT', "862031 2c464f522c22413a42222c583a87",
     "DATA text as typed up to the :, quotes kept whole"),
    ("LOCATE 1,2:X=LOC(1)", "f12031 2c323a58b3ffa4283129", "commands before functions: LOCATE, LOC"),
    ("H=LPEEK(0):E=ERNO", "48b3ffa92830293a45b3ffac", "Super Extended functions start at $A9"),
    ("WIDTH 40:ATTR 1,0", "e220 34303af82031 2c30", "Super Extended commands start at $E2"),
    ("IF A THEN 1 ELSE 2", "852041 20a720312 03a842032", "ELSE gets a : in front"),
    ('print "x"', "7072696e742022782 2", "lower case is not a keyword"),
    ("REM IF THEN", "82204946205448454e", "REM: rest as typed"),
    ("A=B AND C OR NOT D>=E", "41b34220b0204320b120a82044b2b345", "operators are tokens"),
]


def basic_test():
    """The BASIC tokenizer against lines tokenized by a real CoCo, the ROM's
    rules, the program file format, and listing back."""
    results = []
    bad = [t for t, h in BASIC_REAL if basic.crunch(t) != bytes.fromhex(h)]
    results.append(check(not bad, f"{len(BASIC_REAL)} lines tokenized as a real CoCo did"
                         + (f": {bad[0]!r} differs" if bad else "")))
    for text, h, what in BASIC_RULES:
        got = basic.crunch(text)
        results.append(check(got == bytes.fromhex(h.replace(" ", "")),
                             f"tokenize: {what}" + ("" if got == bytes.fromhex(h.replace(" ", ""))
                                                    else f" (got {got.hex()})")))
    prog = basic.tokenize("20 B\n10 A\n30 C\n20  X\n30\n")
    results.append(check(prog == b"\xff\x00\x0e\x26\x07\x00\x0aA\x00\x26\x0d\x00\x14X\x00\x00\x00",
                         "program file: lines in order, a repeat replaces, a bare number deletes, "
                         "links for $2601"))
    results.append(check(basic.tokenize("10 A", base=0x0E01)[3:5] == b"\x0e\x07",
                         "program file: links for another base"))
    text = "".join(f"10 {t}\n".replace("10 ", f"{10 * (i + 1)} ", 1)
                   for i, (t, _) in enumerate(BASIC_REAL))
    results.append(check(basic.detokenize(basic.tokenize(text)) == text,
                         "detokenize lists the lines back as typed (: before ' and ELSE hidden)"))
    results.append(check(decb.autoexec("LOADER", "BIN") == ("AUTOEXEC", "BAS", AUTOEXEC_TOK)
                         and decb.autoexec("LOADER", "BIN", False)[2]
                         == b'10 LOADM"LOADER.BIN":EXEC\r',
                         "AUTOEXEC.BAS tokenized, or ASCII with disk.tokenize: false"))
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


INIT_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
        jsr     dmpkg_init
        std     $0104               D = version, or A = error code
        tfr     cc,a
        sta     $0100               CC: carry in bit 0
        jsr     dmpkg_close
        bra     done

DMPKG_MAGIC equ $444D504B       no assets.inc here
        include "dmpkg.asm"
"""


def init_test():
    """dmpkg_init: finds the SDC and returns its version in D (0 before 113),
    or DMPKG_ENODEV without writing a command when nothing is there; dmpkg_open
    on a machine with no SDC fails the same way."""
    results = []
    with tempfile.TemporaryDirectory() as td:
        prog = lwasm_src(INIT_ASM, td)
    for fw, want in ((0x0129, 0x0129), (0x0117, 0x0117), (0x0110, 0)):
        m = Machine({}, firmware=fw)
        m.run(prog, 0x1000, 0x1002)
        cc, d = m.peek(0x100, 1)[0], int.from_bytes(m.peek(0x104, 2), "big")
        results.append(check(not cc & 1 and d == want and not m.sdc.cmdmode,
                             f"dmpkg_init, firmware {fw:X}: D=${d:04X}, carry {cc & 1}, "
                             "back in emulation mode after dmpkg_close"))
    m = Machine({}, present=False)
    c0 = m.cpu.cycles
    m.run(prog, 0x1000, 0x1002)
    cc, a = m.peek(0x100, 1)[0], m.peek(0x104, 1)[0]
    results.append(check(cc & 1 and a == ENODEV and not m.sdc.log,
                         f"dmpkg_init, no SDC -> DMPKG_ENODEV (A=${a:02X}, carry {cc & 1}, "
                         f"{m.cpu.cycles - c0} cycles, no command written)"))
    return all(results)


FAIL_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
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
            f.write("project:\n  name: Fail\n  image: F.SDS\n  sdc_path: F.SDS\n"
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


def main():
    seed = int(os.environ.get("TEST_SEED", random.randrange(1 << 32)))
    print(f"assets: random, seed {seed} (rerun with TEST_SEED={seed})")
    with tempfile.TemporaryDirectory() as ex:
        shutil.copy(os.path.join(ROOT, "example", "project.yaml"), ex)
        src = mkassets.make(os.path.join(ex, "assets"), seed)
        ok = run_all(ex, src)
        ok &= disk_layout_test()
        ok &= init_test()
        ok &= output_dirs_test()
        ok &= basic_test()
        ok &= decb_word_fail_test()
    ok &= hwtest_test()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    return bool(cond)


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


def verify(path):
    """dmpkg verify, its report kept for failures only."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = cmd_verify(path)
    if not ok:
        print(buf.getvalue(), end="")
    return ok


def read(path):
    with open(path, "rb") as f:
        return f.read()


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

    # ---- run 2: stale-image build_id check
    bprog, bdone = assemble(ex, build, "badid")
    m = Machine(files_on_card, mounted="DEMO.DSK")
    m.run(bprog, 0x1000, bdone)
    chk(m.peek(0x100, 1)[0] == EHDR, "wrong build_id -> DMPKG_EHDR")

    # ---- run 2b: right build_id, wrong magic: not a dmpkg image
    m = Machine({"DEMO.DSK": b"XMPK" + img[4:]}, mounted="DEMO.DSK")
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1)[0] == EHDR, "wrong magic -> DMPKG_EHDR")

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

    # ---- no SDC at all: dmpkg_open stops at the presence check
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
    chk(len(simg) == image.MIN_IMAGE and simg[13] == 0, "stream image: 162 blocks, no disk flag")
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


LAYOUT_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
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


def screen(m):
    """The 32x16 text screen as 16 strings; inverse characters lower-cased."""
    rows = []
    for r in range(16):
        line = ""
        for c in m.peek(0x0400 + 32 * r, 32):
            ch = chr(c & 0x3F if c & 0x20 else (c & 0x3F) | 0x40)
            line += ch if c & 0x40 else ch.lower()
        rows.append(line.rstrip())
    return [r for r in rows if r]


def hwtest_test():
    """The hardware test loaders (make hwtest), run here first: on a CoCo 2
    and a CoCo 3, then with a corrupted asset and with HWTEST.SDS missing."""
    hb = os.path.join(ROOT, "hwtest", "build")
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
            # timing: 10 ticks of restarts (1.39 ms each) and 52 of streaming
            # (6.66 ms a block) in the model; checked against cycle counts
            results.append(check("BLOCK MS   6.7" in cells and "RESTART MS 1.4" in cells
                                 and m.peek(0x010C, 3) == bytes(3) and not m.crb & 1,
                                 f"{what}: timing cells, IRQ hook and PIA put back"))
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


def lwasm_src(text, inc_dir):
    src = os.path.join(inc_dir, "t.asm")
    with open(src, "w") as f:
        f.write(text)
    return lwasm(src, os.path.join(inc_dir, "t.bin"), inc_dir)


if __name__ == "__main__":
    sys.exit(main())
