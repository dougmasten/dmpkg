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

Reads the disks with dmpkg's DECB reader (dmpkg.decb.read_files, the one
behind dmpkg decb copy).

Usage: extract.py   (run from anywhere; paths are relative to this file)
"""
import hashlib, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# dmpkg's packer: the Makefile puts it on PYTHONPATH; else the repo's copy
sys.path.append(os.path.join(HERE, '..', '..', 'python', 'src'))
from dmpkg.decb import read_files          # noqa: E402
from dmpkg.errors import PackError         # noqa: E402

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


def files(path):
    """{name.ext: bytes} for every file on a Disk BASIC image (dmpkg's reader,
    the same as dmpkg decb copy)."""
    try:
        return {f['name']: f['data'] for f in read_files(open(path, 'rb').read(), path)}
    except PackError as e:
        sys.exit(str(e))


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
