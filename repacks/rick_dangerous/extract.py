#!/usr/bin/env python3
"""Pull the game files off the original disks in bin/ into extracted/.

Reads bin/RICK1.DSK, RICK2.DSK and RICK3.DSK (35-track Disk BASIC images) and
copies out only the files the repack is built from:

  SPLASH0-3.BIN       splash screen, pages $00-$03
  COMP0000-0049.BIN   the compressed game stream

RICK.BAS, MOVER.BIN and CC3X0.BIN (the original loader) are not needed: the
repack has its own loader, and mkassets.py decompresses the stream itself.
Nothing outside this directory is used.

Each disk is checked against the SHA-256 of the known release first, so a
different dump or release stops here instead of building a broken image.

Usage: extract.py   (run from anywhere; paths are relative to this file)
"""
import hashlib, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'bin')
OUT = os.path.join(HERE, 'extracted')

# SHA-256 of the original disks the repack is built and tested from
DISKS = {
    'RICK1.DSK': '92c5af8b0d96d205ab2999da3e79488f0f5de8bd165d021762412a31e8f87c6e',
    'RICK2.DSK': 'fed0e62379ad0315a7ce37a227365bfe4e70fed7d6e58b6b6428b57ab7d360bf',
    'RICK3.DSK': 'e37290da24297b750db1634e5e2bb24965cd775449d1a8e937d1568193d866ad',
}
WANT = re.compile(r'^(SPLASH[0-3]|COMP\d{4})\.BIN$')
EXPECT = {f'SPLASH{n}.BIN' for n in range(4)} | {f'COMP{n:04d}.BIN' for n in range(50)}

SECTOR, SPT, TRACKS = 256, 18, 35
DIR_TRACK = 17


def sector(img, track, sec):
    """Bytes of one sector (sec is 1-based)."""
    o = (track * SPT + sec - 1) * SECTOR
    return img[o:o + SECTOR]


def granule(img, g):
    """Bytes of one 9-sector granule; granules skip the directory track."""
    track = g // 2
    if track >= DIR_TRACK:
        track += 1
    o = (track * SPT + (g % 2) * 9) * SECTOR
    return img[o:o + 9 * SECTOR]


def files(path):
    """{name.ext: bytes} for every file on a Disk BASIC image."""
    img = open(path, 'rb').read()
    if len(img) != TRACKS * SPT * SECTOR:
        sys.exit(f'{path}: not a 35-track image ({len(img)} bytes)')
    fat = sector(img, DIR_TRACK, 2)[:68]
    dirdata = b''.join(sector(img, DIR_TRACK, s) for s in range(3, 12))
    out = {}
    for i in range(0, len(dirdata), 32):
        e = dirdata[i:i + 32]
        if e[0] == 0xFF:
            break
        if e[0] == 0:
            continue
        name = e[0:8].decode('ascii').rstrip() + '.' + e[8:11].decode('ascii').rstrip()
        g, last = e[13], e[14] << 8 | e[15]
        data = bytearray(); seen = set()
        while True:
            if g >= 68 or g in seen:
                sys.exit(f'{path}: {name}: bad granule chain')
            seen.add(g)
            nxt = fat[g]
            if nxt >= 0xC0:                       # last granule: low bits = sectors used
                n = nxt & 0x1F
                if not 1 <= n <= 9:
                    sys.exit(f'{path}: {name}: bad last-granule entry ${nxt:02X}')
                data += granule(img, g)[:(n - 1) * SECTOR + last]
                break
            data += granule(img, g)
            g = nxt
        out[name] = bytes(data)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    got = set()
    for d, sha in DISKS.items():
        path = os.path.join(SRC, d)
        if not os.path.exists(path):
            sys.exit(f'missing {path}')
        got_sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        if got_sha != sha:
            sys.exit(f'{path}: SHA-256 {got_sha}\n  expected {sha}\n'
                     '  (not the disk this repack is built from)')
        for name, data in sorted(files(path).items()):
            if not WANT.match(name):
                continue
            if name in got:
                sys.exit(f'{name} is on more than one disk')
            open(os.path.join(OUT, name), 'wb').write(data)
            got.add(name)
    if got != EXPECT:
        sys.exit('missing: ' + ' '.join(sorted(EXPECT - got)))
    print(f'{len(got)} files from {", ".join(DISKS)} -> extracted/')


main()
