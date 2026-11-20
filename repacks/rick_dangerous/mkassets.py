#!/usr/bin/env python3
"""Cut the Rick Dangerous game into dmpkg assets and write project.yaml.

Input is only extracted/ (made from bin/RICK1-3.DSK by extract.py).

The original loader runs COMP0000-0049.BIN through the CC3X0 decompressor
into physical RAM.  This script replays that decompression (the same
algorithm as CC3X0), merges the blocks it writes into contiguous physical
runs, and writes each run as a raw asset with a phys: address, so the dmpkg
loader streams the same bytes to the same places.  The splash screen
(SPLASH0-3.BIN, which MOVER copies into pages 0-3) comes first, so it can be
shown while the rest loads.

Left out on purpose:
  - the 7-byte stub in page $38 at $0000 and the boot stub in page $FF at
    $FD00: they only hand over from the original loader to the game's Boot,
    which LOADER.ASM does itself
  - page $3E: not in the stream; MOVER copies page $38 there, and so does
    LOADER.ASM

Usage: mkassets.py   (run from anywhere; paths are relative to this file)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(HERE, 'extracted')
OUT = os.path.join(HERE, 'assets')

SKIP_PAGES = {0x38, 0xFF}       # loader hand-over stubs, see above


def loadm(path):
    """Segments of a LOADM file as [(addr, bytes)]."""
    d = open(path, 'rb').read(); i = 0; segs = []
    while i < len(d):
        t, n, a = d[i], d[i+1] << 8 | d[i+2], d[i+3] << 8 | d[i+4]
        if t == 0xFF:
            return segs
        if t != 0:
            sys.exit(f'{path}: bad LOADM record')
        segs.append((a, d[i+5:i+5+n])); i += 5 + n
    sys.exit(f'{path}: no postamble')


class Bits:
    """CC3X0 bit reader: flag bits MSB first, interleaved Elias gamma."""
    def __init__(s, data): s.d = data; s.p = 0; s.mask = 0; s.bits = 0

    def byte(s):
        if s.p >= len(s.d):
            sys.exit('decode error: stream ends inside a block')
        b = s.d[s.p]; s.p += 1; return b

    def word(s):
        return s.byte() << 8 | s.byte()

    def bit(s):
        if s.mask == 0:
            s.bits = s.byte(); s.mask = 0x80
        r = 1 if s.bits & s.mask else 0
        s.mask >>= 1; return r

    def gamma(s):
        v = 1
        while not s.bit():
            v = v << 1 | s.bit()
        return v


def decompress():
    """Replay CC3X0 over COMP0000-0049.BIN.  Returns (2 MB physical RAM image,
    [(phys start, phys end)] of every block written)."""
    mem = bytearray(0x200000); valid = bytearray(0x200000)
    blocks = []; left = None
    for n in range(50):
        fn = f'COMP{n:04d}.BIN'
        segs = dict(loadm(os.path.join(EXT, fn)))
        if 0x0F04 not in segs or 0x2000 not in segs:
            sys.exit(f'{fn}: expected segments at $0F04 and $2000')
        ln = segs[0x0F04][0] << 8 | segs[0x0F04][1]
        st = Bits(segs[0x2000][:ln])
        if left is None:            # stream header: magic, block count, 35 more bytes
            if bytes(st.byte() for _ in range(6)) != b'CC3X03':
                sys.exit(f'{fn}: bad magic')
            left = st.word()
            for _ in range(2 + 8 + 8 + 4 + 1 + 10 + 2):
                st.byte()
        while left and st.p < len(st.d):
            left -= 1
            page = st.byte(); off = st.word(); length = st.word(); st.word()
            st.mask = 0; ref = None
            dst = start = page * 0x2000 + off; remaining = length
            while remaining > 0:
                if st.bit():                        # copy from earlier output
                    far = st.bit(); k = st.gamma()
                    if far:
                        ref = st.byte() * 0x2000 + st.word()
                    elif ref is None:
                        sys.exit(f'{fn}: decode error: copy with no source')
                    for i in range(k):              # byte by byte: may overlap
                        if not valid[ref + i]:
                            sys.exit(f'{fn}: decode error: copy from unwritten memory')
                        mem[dst + i] = mem[ref + i]; valid[dst + i] = 1
                else:                               # literal run
                    k = st.gamma()
                    mem[dst:dst + k] = bytes(st.byte() for _ in range(k))
                    valid[dst:dst + k] = b'\x01' * k
                dst += k; remaining -= k
            if remaining:
                sys.exit(f'{fn}: decode error: block overrun')
            blocks.append((page, start, start + length))
    if left:
        sys.exit(f'stream ended with {left} blocks left')
    return mem, blocks


def main():
    mem, blocks = decompress()

    iv = sorted((s, e) for page, s, e in blocks if page not in SKIP_PAGES)
    runs = []
    for s, e in iv:
        if runs and s <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], e)
        else:
            runs.append([s, e])
    for s, e in runs:   # page $3E must stay out: it's built from page $38
        if s < 0x7E000 and e > 0x7C000:
            sys.exit('stream writes page $3E')

    os.makedirs(OUT, exist_ok=True)

    # splash: four LOADM files at $2000, one 8K page each (the last is short)
    splash = bytearray()
    for n in range(4):
        segs = loadm(os.path.join(EXT, f'SPLASH{n}.BIN'))
        if len(segs) != 1 or segs[0][0] != 0x2000:
            sys.exit(f'SPLASH{n}.BIN: expected one segment at $2000')
        if n < 3 and len(segs[0][1]) != 0x2000:
            sys.exit(f'SPLASH{n}.BIN: expected 8K')
        splash += segs[0][1]
    open(os.path.join(OUT, 'splash.bin'), 'wb').write(splash)

    lines = [
        '# Rick Dangerous (CoCo 3, Glen Hewlett) as one dmpkg disk image.',
        '# Generated by mkassets.py from extracted/ (bin/RICK1-3.DSK) - do not edit.',
        '',
        'project:',
        '  name: Rick Dangerous',
        '  image: build/RICK.DSK',
        '  equates: build/assets.inc',
        '  table: build/assettab.asm',
        '  map: build/RICK.map',
        '  disk:',
        '    loader: build/RICK.BIN',
        '    autoexec: true',
        '    reserve: 2              # RICK.BIN and AUTOEXEC.BAS: the assets fill the disk',
        '    data: RICK.DAT          # owns the asset granules (DIR shows it)',
        '',
        '# LOADER.ASM loads these in order: splash first, then every run.',
        'assets:',
        '  - name: splash',
        '    file: assets/splash.bin',
        '    phys: 0x000000          # pages $00-$03',
    ]
    total = len(splash)
    for s, e in runs:
        name = f'p{s:06X}'
        open(os.path.join(OUT, name + '.bin'), 'wb').write(mem[s:e])
        lines += [f'  - name: {name}',
                  f'    file: assets/{name}.bin',
                  f'    phys: 0x{s:06X}          # page ${s >> 13:02X} +${s & 0x1FFF:04X}, {e - s} bytes']
        total += e - s
    open(os.path.join(HERE, 'project.yaml'), 'w').write('\n'.join(lines) + '\n')
    print(f'{len(runs) + 1} assets, {total} bytes')


main()
