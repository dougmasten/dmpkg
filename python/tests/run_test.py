#!/usr/bin/env python3
"""
Run every test: make test runs this after building the example, the
runtime and the hardware test disks. Each module also runs on its own
(python3 python/tests/test_basic.py, for example).

  harness.py        the SDC model, the 6809 machine, helpers (no tests)
  test_runtime.py   the runtime end to end on the example project
  test_disk.py      the packer's disk layout and output folders
  test_init.py      dmpkg_init: presence, version, firmware gate
  test_basic.py     the BASIC tokenizer
  test_decb.py      a DECB length word split across a failing block
  test_hwtest.py    the hardware and stress test loaders

TEST_SEED=n repeats test_runtime's random assets.
"""
import sys

import test_basic
import test_decb
import test_disk
import test_hwtest
import test_init
import test_runtime


def main():
    ok = test_runtime.run()
    ok &= test_disk.run()
    ok &= test_init.run()
    ok &= test_basic.run()
    ok &= test_decb.run()
    ok &= test_hwtest.run()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
