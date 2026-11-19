# dmpkg - common tasks
#
#   make            same as make test
#   make test       build + verify the example, then the 6809 end-to-end test
#                   (which makes its own random assets; TEST_SEED=n repeats a run)
#   make build      generate the example assets, build the include files,
#                   assemble the loader, then build the disk image with it
#   make verify     check the example image's size, CRCs and build ID
#   make list       print the example image's TOC
#   make asm        assemble the runtime for 6809 and 6309, report its size
#   make hwtest     build the hardware test disks (docs/HWTEST.md)
#   make clean      remove generated files (example assets included)
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
MANIFEST := $(EXAMPLE)/project.yaml
BUILD    := $(EXAMPLE)/build
IMAGE    := $(BUILD)/DEMO.DSK
LOADER   := $(BUILD)/LOADER.BIN
PASS1    := $(BUILD)/.pass1
RUNTIME  := asm/dmpkg.asm
ASSETS   := $(EXAMPLE)/assets/.stamp
OUT      := build

.PHONY: all test build verify list asm hwtest clean

all: test

$(ASSETS): $(EXAMPLE)/mkassets.py
	$(PYTHON) $(EXAMPLE)/mkassets.py
	@touch $@

# Two passes: the loader embeds the build_id and descriptors from the include
# files, so they're written first (pass 1), then the loader is assembled and
# the image rebuilt with it on the disk. Asset placement and build_id don't
# depend on the loader, so pass 2 writes the same include files.
$(PASS1): $(PACKER) $(MANIFEST) $(ASSETS)
	$(PYTHON) -m dmpkg build $(MANIFEST) --no-loader
	@touch $@

$(LOADER): $(EXAMPLE)/loader.asm $(RUNTIME) $(PASS1)
	$(LWASM) --6809 --format=decb -I asm -I $(BUILD) -o $@ $(EXAMPLE)/loader.asm

$(IMAGE): $(PASS1) $(LOADER)
	$(PYTHON) -m dmpkg build $(MANIFEST) -v

build: $(IMAGE)

verify: $(IMAGE)
	$(PYTHON) -m dmpkg verify $(IMAGE)

list: $(IMAGE)
	$(PYTHON) -m dmpkg list $(IMAGE)

# Assembled at $(ASM_ORG), not 0: at 0 the runtime's variables would land in
# the direct page and come out smaller than in a real program.
ASM_ORG ?= 0x2000

asm: $(RUNTIME)
	@mkdir -p $(OUT)
	@printf 'DMPKG_MAGIC equ $$444D504B\n org %s\n include "%s"\n' '$(ASM_ORG)' '$(CURDIR)/$(RUNTIME)' > $(OUT)/runtime.asm
	$(LWASM) --6809 --format=raw -o $(OUT)/dmpkg.bin --list=$(OUT)/dmpkg.lst $(OUT)/runtime.asm
	$(LWASM) --6309 --format=raw -o $(OUT)/dmpkg.6309.bin $(OUT)/runtime.asm
	$(LWASM) --6809 --format=raw -DDMPKG_NOVERCHK -o $(OUT)/dmpkg.nover.bin $(OUT)/runtime.asm
	@echo "runtime: $$(wc -c < $(OUT)/dmpkg.bin) bytes" \
	      "($$(wc -c < $(OUT)/dmpkg.nover.bin) with DMPKG_NOVERCHK), assembled at" '$(ASM_ORG)'

test: verify asm hwtest
	$(PYTHON) python/tests/run_test.py

# Hardware test (docs/HWTEST.md): HWTEST.SDS is mounted by name, and the two
# disks' loaders include its symbols, so it's built first. Each disk is then
# built in two passes like the example.
HW      := hwtest
HWB     := $(HW)/build
HWSTAMP := $(HWB)/.assets
HWSDS   := $(HWB)/HWTEST.SDS
HWDISKS := $(HWB)/HWTEST.DSK $(HWB)/HWBIG.DSK

$(HWSTAMP): $(HW)/mkhw.py $(HW)/disk.yaml $(HW)/big.yaml $(HW)/stream.yaml $(PACKER)
	$(PYTHON) $(HW)/mkhw.py
	@touch $@

$(HWSDS): $(HWSTAMP)
	$(PYTHON) -m dmpkg build $(HW)/stream.yaml

$(HWB)/.pass1-%: $(HWSTAMP)
	$(PYTHON) -m dmpkg build $(HW)/$*.yaml --no-loader
	@touch $@

$(HWB)/HWTEST.BIN: $(HW)/hwtest.asm $(RUNTIME) $(HWSDS) $(HWB)/.pass1-disk
	$(LWASM) --6809 --format=decb -I asm -I $(HWB)/disk -I $(HWB) -o $@ $<

$(HWB)/HWBIG.BIN: $(HW)/hwtest.asm $(RUNTIME) $(HWSDS) $(HWB)/.pass1-big
	$(LWASM) --6809 --format=decb -DHWBIG -I asm -I $(HWB)/big -I $(HWB) -o $@ $<

$(HWB)/HWTEST.DSK: $(HWB)/HWTEST.BIN
	$(PYTHON) -m dmpkg build $(HW)/disk.yaml

$(HWB)/HWBIG.DSK: $(HWB)/HWBIG.BIN
	$(PYTHON) -m dmpkg build $(HW)/big.yaml

hwtest: $(HWSDS) $(HWDISKS)
	@for f in $(HWSDS) $(HWDISKS); do $(PYTHON) -m dmpkg verify $$f || exit 1; done
	@echo "copy $(HWSDS) $(HWDISKS) to one folder on the SD card (docs/HWTEST.md)"

clean:
	rm -rf $(BUILD) $(OUT) $(EXAMPLE)/assets $(HWB) $(HW)/assets
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
