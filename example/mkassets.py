#!/usr/bin/env python3
"""
Write the example project's assets: random filler with the sizes and shapes
project.yaml and the tests rely on.

  mkassets.py [DIR] [--seed N]

DIR defaults to the assets/ folder next to this script. The default seed is
fixed, so the example image (and the worked example in docs/FORMAT.md) comes
out the same every time; the test passes its own seed.

  font.bin     767 bytes   odd length: the packed palette lands at offset 255
  palette.bin   16 bytes   packed after font
  level1.bin  1500 bytes   spans 3 blocks
  map.bin     1920 bytes
  title.gfx  20001 bytes   phys load at $060001, crosses three 8K windows (aligned)
  code.bin    LOADM file   301 bytes at $3000, 77 bytes at $3400, exec $3000
"""
import argparse
import os
import random

DEFAULT_SEED = 6809

RAW = {"font.bin": 767, "palette.bin": 16, "level1.bin": 1500,
       "map.bin": 1920, "title.gfx": 20001}
CODE_SEGS = ((0x3000, 301), (0x3400, 77))
CODE_EXEC = 0x3000


def rand_bytes(rng: random.Random, n: int) -> bytes:
    return rng.getrandbits(8 * n).to_bytes(n, "little")


def make(out_dir: str, seed: int = DEFAULT_SEED) -> dict:
    """Write the assets to out_dir; return {filename: bytes}."""
    rng = random.Random(seed)
    files = {name: rand_bytes(rng, n) for name, n in RAW.items()}
    code = b""
    for addr, n in CODE_SEGS:
        code += b"\x00" + n.to_bytes(2, "big") + addr.to_bytes(2, "big") + rand_bytes(rng, n)
    files["code.bin"] = code + b"\xff\x00\x00" + CODE_EXEC.to_bytes(2, "big")
    os.makedirs(out_dir, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(data)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("dir", nargs="?",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = ap.parse_args()
    make(a.dir, a.seed)


if __name__ == "__main__":
    main()
