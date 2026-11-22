# dmpkg - common tasks
#
#   make            same as make test
#   make test       build + verify the example (example/Makefile), then the
#                   6809 end-to-end test (which makes its own random assets;
#                   TEST_SEED=n repeats a run)
#   make asm        assemble the runtime for 6809 and 6309, report its size
#   make hwtest     build the hardware and stress test disks (hwtest/Makefile,
#                   docs/HWTEST.md)
#   make clean      remove generated files (example's included)
#
# The example has its own Makefile: cd example && make (build, verify, list).
#
# The repo has two parts: the packer (python/, a Python project of its own)
# and the 6809 runtime (asm/). The Makefile runs the packer from python/src
# (python3 -m dmpkg); pip install -e python puts a dmpkg command on PATH.
#
# Override tools on the command line: make test LWASM=/path/to/lwasm

PYTHON  ?= python3
LWASM   ?= lwasm
export LWASM

# Run the packer from python/src without installing it
# (pip install -e python also works)
export PYTHONPATH := $(CURDIR)/python/src$(if $(PYTHONPATH),:$(PYTHONPATH))
PACKER  := $(wildcard python/src/dmpkg/*.py)

EXAMPLE  := example
RUNTIME  := asm/dmpkg.asm
OUT      := build

.PHONY: all test example asm hwtest clean

all: test

# the example builds with its own Makefile
example:
	$(MAKE) -C $(EXAMPLE) verify PYTHON='$(PYTHON)' LWASM='$(LWASM)'

# Assembled at $(ASM_ORG), not 0: at 0 the runtime's variables would land in
# the direct page and come out smaller than in a real program.
ASM_ORG ?= 0x2000

asm: $(RUNTIME)
	@mkdir -p $(OUT)
	@printf ' org %s\n include "%s"\n' '$(ASM_ORG)' '$(CURDIR)/$(RUNTIME)' > $(OUT)/runtime.asm
	$(LWASM) --6809 --format=raw -o $(OUT)/dmpkg.bin --list=$(OUT)/dmpkg.lst $(OUT)/runtime.asm
	$(LWASM) --6309 --format=raw -o $(OUT)/dmpkg.6309.bin $(OUT)/runtime.asm
	@echo "runtime: $$(wc -c < $(OUT)/dmpkg.bin) bytes, assembled at" '$(ASM_ORG)'

test: example asm hwtest
	$(PYTHON) python/tests/run_test.py

# Hardware and stress tests (docs/HWTEST.md), each with its own Makefile
# under hwtest/
hwtest:
	$(MAKE) -C hwtest PYTHON='$(PYTHON)' LWASM='$(LWASM)'

clean:
	$(MAKE) -C $(EXAMPLE) clean
	$(MAKE) -C hwtest clean
	rm -rf $(OUT)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
