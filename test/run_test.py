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
"""
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile

logging.disable(logging.CRITICAL)
from MC6809.components.cpu6809 import CPU
from MC6809.components.memory import Memory
from MC6809.tests.test_config import TestCfg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import dmpkg  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "example"))
import mkassets  # noqa: E402

LWASM = os.environ.get("LWASM", "lwasm")
READY_LAG = 20          # cycles READY stays stale after a block is drained
FILL_TIME = 400         # cycles to "read" the next block from the card
CMD_SETTLE = 36         # 20us at 1.79MHz: no register access after a command


class SDCError(Exception):
    pass


class MockSDC:
    def __init__(self, cpu, files, firmware=0x0127):
        self.cpu, self.files = cpu, files
        self.firmware = firmware      # BCD, e.g. 0x0127
        self.version_reply = None     # bytes readable at $FF4A/$FF4B
        self.cmd_at = -CMD_SETTLE
        self.fail_block = None        # block number whose read fails
        self.fail_status = 0x08       # firmware: stream ends, $08, no FAILED bit
        self.stall_block = None       # block where the stream hangs (BUSY, never READY)
        self.cmdmode = False
        self.params = [0, 0, 0]
        self.status = 0
        self.mounted = None
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
    def settle_check(self, addr):
        if self.now - self.cmd_at < CMD_SETTLE:
            raise SDCError(f"read ${addr:04X} {self.now - self.cmd_at} cycles after a command")

    def read_stat(self, *_):
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
                dsk = img is not None and len(img) >= dmpkg.MIN_IMAGE and len(img) % 256 == 0
                self.status = 0x00 if dsk else 0x80   # not found, or too small for a DSK
                self.log.append(("mount", name))
            return v
        if self.stream is not None and addr in (0xFF4A, 0xFF4B):
            raise SDCError("write to data port during stream")
        self.params[addr - 0xFF49] = v
        return v

    def read_data(self, _c, _a, addr):
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
    def __init__(self, files, firmware=0x0127):
        cfg = TestCfg({"verbosity": None, "display_cycle": False, "trace": None,
                       "bus_socket_host": None, "bus_socket_port": None, "ram": None,
                       "rom": None, "max_ops": None, "use_bus": False})
        self.mem = Memory(cfg)
        self.cpu = CPU(self.mem, cfg)
        self.sdc = MockSDC(self.cpu, files, firmware)
        self.phys = bytearray(0x200000)
        self.mmu = 0x3A                       # arbitrary home block for $4000
        m = self.mem
        m.add_write_byte_callback(self.sdc.write_latch, 0xFF40)
        m.add_write_byte_callback(self.sdc.write_cmd, 0xFF48)
        m.add_read_byte_callback(self.sdc.read_stat, 0xFF48)
        m.add_write_byte_callback(self.sdc.write_param, 0xFF49, 0xFF4B)
        m.add_read_byte_callback(self.sdc.read_data, 0xFF4A, 0xFF4B)
        m.add_read_byte_callback(lambda *_: self.mmu, 0xFFA2)
        m.add_write_byte_callback(self._mmu_w, 0xFFA2)
        m.add_read_byte_callback(self._win_r, 0x4000, 0x5FFF)
        m.add_write_byte_callback(self._win_w, 0x4000, 0x5FFF)

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
            self.cpu.get_and_call_next_op()
        raise RuntimeError("program did not finish")

    def peek(self, a, n):
        return bytes(self.mem.read_byte(a + i) for i in range(n))


TEST_ASM = r"""
        org     $1000
        bra     start
done    bra     done                fixed at $1002 - harness stops here
start
        ldx     #SDC_MountName
        ldy     #{hdr}
        jsr     sdc_open
        lbcs    fail
        ldx     #FONT               in image order...
        jsr     sdc_load
        lbcs    fail
        ldx     #PALETTE            ...packed behind it, no restart expected
        jsr     sdc_load
        lbcs    fail
        ldx     #TITLE              forward seek (aligned), phys over 3 windows
        jsr     sdc_load
        lbcs    fail
        ldx     #LEVEL1             backward seek + skip to offset 271
        jsr     sdc_load
        lbcs    fail
        ldx     #MAP                packed behind LEVEL1, no restart expected
        jsr     sdc_load
        lbcs    fail
        ldx     #CODE               decb at offset 33, returns exec in U
        jsr     sdc_load
        lbcs    fail
        stu     $0102
        ldx     #$6400              wipe PALETTE, then reload it out of order:
        ldb     #16                 restart at its block, skip its odd offset
wipe    clr     ,x+
        decb
        bne     wipe
        ldx     #PALETTE
        jsr     sdc_load
        lbcs    fail
        jsr     sdc_close
        clra
fail    sta     $0100
        ldd     sdc_ver
        std     $0104
        bra     done

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
badid   fcb     $12,$34,$56,$78
"""


def assemble(src_dir, build_dir, hdr_label, defines=()):
    asm = os.path.join(build_dir, "test.asm")
    with open(asm, "w") as f:
        f.write(TEST_ASM.replace("{hdr}", hdr_label))
    out = os.path.join(build_dir, "test.bin")
    lst = os.path.join(build_dir, "test.lst")
    subprocess.run([LWASM, "--6809", "--format=raw", "-o", out, f"--list={lst}",
                    *[f"-D{d}" for d in defines],
                    "-I", os.path.join(ROOT, "runtime"), "-I", build_dir, asm], check=True)
    with open(out, "rb") as f:
        return f.read(), 0x1002


FAIL_ASM = r"""
        org     $1000
        bra     start
done    bra     done
start
        ldx     #SDC_MountName
        ldy     #SDC_BuildID
        jsr     sdc_open
        lbcs    fail
        ldx     #CODE               length word straddles blocks 1/2
        jsr     sdc_load
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
    across blocks 1 and 2, and block 2 fails. sdc_getword must return the
    error code (SDC_EFAIL), not the length's first byte."""
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
        res = dmpkg.build(os.path.join(td, "p.yaml"))
        code = {a.name: a for a in res["assets"]}["CODE"]
        assert (code.block, code.off) == (1, 511), (code.block, code.off)
        with open(res["image"], "rb") as f:
            img = f.read()
        asm = os.path.join(td, "t.asm")
        with open(asm, "w") as f:
            f.write(FAIL_ASM)
        out = os.path.join(td, "t.bin")
        subprocess.run([LWASM, "--6809", "--format=raw", "-o", out,
                        "-I", os.path.join(ROOT, "runtime"), "-I", td, asm], check=True)
        with open(out, "rb") as f:
            prog = f.read()
    results = []
    for status, how in ((0x08, "stream ends with $08"), (0x81, "FAILED bit")):
        m = Machine({"F.SDS": img})
        m.sdc.fail_block = 2
        m.sdc.fail_status = status
        m.run(prog, 0x1000, 0x1002)
        err = m.peek(0x100, 1)[0]
        results.append(check(err == 5, f"decb word split across a failing block ({how}) "
                                       f"-> SDC_EFAIL (A=${err:02X})"))
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
        ok &= decb_word_fail_test()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    return bool(cond)


def run_all(ex, src):
    res = dmpkg.build(os.path.join(ex, "project.yaml"))
    build = os.path.join(ex, "build")
    with open(res["image"], "rb") as f:
        img = f.read()
    files = {"GAMES/DEMO.SDS": img}
    A = {a.name: a for a in res["assets"]}
    results = []

    def chk(cond, msg):
        results.append(check(cond, msg))

    # ---- run 1: full load
    prog, done = assemble(ex, build, "SDC_BuildID")
    m = Machine(files)
    c0 = m.cpu.cycles
    m.run(prog, 0x1000, done)
    print(f"run 1: {m.cpu.cycles - c0} cycles, SDC log {m.sdc.log}")
    chk(m.peek(0x100, 1) == b"\0", f"no error (A=${m.peek(0x100,1)[0]:02X})")
    chk(m.peek(0x6000, 767) == src["font.bin"], "FONT bytes")
    chk(m.peek(0x6400, 16) == src["palette.bin"], "PALETTE bytes (packed, odd offset)")
    chk(m.peek(0x6500, 1500) == src["level1.bin"], "LEVEL1 bytes")
    chk(m.peek(0x7000, 1920) == src["map.bin"], "MAP bytes")
    t = src["title.gfx"]
    chk(bytes(m.phys[0x7A001:0x7A001 + len(t)]) == t, "TITLE in physical RAM across MMU windows")
    chk(m.phys[0x7A000] == 0 and m.phys[0x7A001 + len(t)] == 0, "TITLE no overrun either side")
    chk(m.mmu == 0x3A, "MMU window restored")
    for addr, data in A["CODE"].segs:
        chk(m.peek(addr, len(data)) == data, f"CODE segment ${addr:04X} ({len(data)} bytes)")
    chk(m.peek(0x102, 2) == b"\x30\x00", "decb exec returned in U")
    starts = [e for e in m.sdc.log if e[0] == "stream"]
    chk(starts == [("stream", 0), ("stream", 1), ("stream", 10), ("stream", 2), ("stream", 49), ("stream", 2)],
          f"stream restarts only where needed {starts}")
    chk(m.peek(0x6400, 16) == src["palette.bin"],
          "PALETTE reloaded out of order (seek + skip 255 bytes)")
    chk(m.sdc.stream is None and not m.sdc.cmdmode, "closed, back in emulation mode")

    # ---- run 2: stale-image build_id check
    prog, done = assemble(ex, build, "badid")
    m = Machine(files)
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1) == b"\x02", "wrong build_id -> SDC_EHDR")

    # ---- run 3: missing image
    prog, done = assemble(ex, build, "SDC_BuildID")
    m = Machine({"OTHER.SDS": img})
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1) == b"\x01", "missing file -> SDC_EMOUNT")

    # ---- run 3b: image one block under the DSK minimum
    m = Machine({"GAMES/DEMO.SDS": img[:dmpkg.MIN_IMAGE - 512]})
    m.run(prog, 0x1000, done)
    chk(m.peek(0x100, 1) == b"\x01", "image under 82,944 bytes -> SDC_EMOUNT")

    # ---- run 3c: card hangs part way through TITLE (phys load): READY never
    # comes, so sdc_waitready times out. SDC_TIMEOUT=1 (65536 polls) keeps
    # the run short; the MMU window must be put back even on the error.
    tprog, tdone = assemble(ex, build, "SDC_BuildID", defines=["SDC_TIMEOUT=1"])
    m = Machine(files)
    m.sdc.stall_block = A["TITLE"].block + 1
    c0 = m.cpu.cycles
    m.run(tprog, 0x1000, tdone, max_ops=50_000_000)
    err = m.peek(0x100, 1)[0]
    chk(err == 4, f"card stalls in TITLE -> SDC_ETIME (A=${err:02X}, "
                  f"{m.cpu.cycles - c0} cycles)")
    chk(m.mmu == 0x3A, "  MMU window restored after the error")
    chk(m.sdc.stream is None and ("abort",) in m.sdc.log, "  stream stopped after the error")
    chk(m.peek(0x6000, 767) == src["font.bin"], "  assets before it still loaded")

    # ---- run 4-6: firmware version gate (117 added STREAM, 113 VERSION)
    for fw, want, what in ((0x0117, 0, "firmware 117 accepted"),
                           (0x0116, 6, "firmware 116 -> SDC_EVER"),
                           (0x0110, 6, "firmware before VERSION -> SDC_EVER")):
        m = Machine(files, firmware=fw)
        m.run(prog, 0x1000, done)
        err = m.peek(0x100, 1)[0]
        ver = int.from_bytes(m.peek(0x104, 2), "big")
        expect_ver = fw if fw >= 0x0113 else 0
        chk(err == want and ver == expect_ver,
              f"{what} (A=${err:02X}, sdc_ver=${ver:04X})")
        if want:
            chk(not any(e[0] in ("mount", "stream") for e in m.sdc.log),
                  "  no mount or stream attempted")

    return all(results)


if __name__ == "__main__":
    sys.exit(main())
