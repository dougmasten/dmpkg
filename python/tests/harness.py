#!/usr/bin/env python3
"""
The test harness the test_*.py modules share: a strict CoCo SDC model, a
6809 machine with a CoCo 3 MMU window and a 60 Hz field sync, and helpers
(lwasm, a DECB directory reader, the text screen, PASS/FAIL checks).

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
"""
import contextlib
import io
import logging
import os
import random
import re
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
    from dmpkg.cli import main as cli_main  # noqa: E402
    from dmpkg.errors import PackError  # noqa: E402
except ImportError:
    sys.exit("tests: can't import dmpkg: pip install -e python or set "
             "PYTHONPATH=python/src (make test does)")

sys.path.insert(0, os.path.join(ROOT, "example"))
import mkassets  # noqa: E402  (the example's asset generator)

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


# 10 LOADM"LOADER.BIN":EXEC as the ROM tokenizes it: LOAD ($D3) then M, the
# name as typed, EXEC ($A2); one line linked for $2601, then $00 $00
AUTOEXEC_TOK = (b"\xff\x00\x17" + b"\x26\x16\x00\x0a" + b"\xd3M" + b'"LOADER.BIN"'
                + b":\xa2\x00" + b"\x00\x00")


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    return bool(cond)


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


def lwasm_src(text, inc_dir):
    src = os.path.join(inc_dir, "t.asm")
    with open(src, "w") as f:
        f.write(text)
    return lwasm(src, os.path.join(inc_dir, "t.bin"), inc_dir)
