# dmpkg - common tasks
#
#   make            same as make test
#   make test       build + verify the example, then the 6809 end-to-end test
#                   (which makes its own random assets; TEST_SEED=n repeats a run)
#   make build      generate the example assets, build the image and include files
#   make verify     check the example image's size, CRCs and build ID
#   make list       print the example image's TOC
#   make asm        assemble the runtime for 6809 and 6309, report its size
#   make clean      remove generated files (example assets included)
#
# Override tools on the command line: make test LWASM=/path/to/lwasm

PYTHON  ?= python3
LWASM   ?= lwasm
export LWASM

EXAMPLE  := example
MANIFEST := $(EXAMPLE)/project.yaml
BUILD    := $(EXAMPLE)/build
IMAGE    := $(BUILD)/DEMO.SDS
RUNTIME  := runtime/dmpkg.asm
ASSETS   := $(EXAMPLE)/assets/.stamp
OUT      := build

.PHONY: all test build verify list asm clean

all: test

$(ASSETS): $(EXAMPLE)/mkassets.py
	$(PYTHON) $(EXAMPLE)/mkassets.py
	@touch $@

$(IMAGE): dmpkg.py $(MANIFEST) $(ASSETS)
	$(PYTHON) dmpkg.py build $(MANIFEST) -v

build: $(IMAGE)

verify: $(IMAGE)
	$(PYTHON) dmpkg.py verify $(IMAGE)

list: $(IMAGE)
	$(PYTHON) dmpkg.py list $(IMAGE)

# Assembled at $(ASM_ORG), not 0: at 0 the runtime's variables would land in
# the direct page and come out smaller than in a real program.
ASM_ORG ?= 0x2000

asm: $(RUNTIME)
	@mkdir -p $(OUT)
	@printf ' org %s\n include "%s"\n' '$(ASM_ORG)' '$(CURDIR)/$(RUNTIME)' > $(OUT)/runtime.asm
	$(LWASM) --6809 --format=raw -o $(OUT)/dmpkg.bin --list=$(OUT)/dmpkg.lst $(OUT)/runtime.asm
	$(LWASM) --6309 --format=raw -o $(OUT)/dmpkg.6309.bin $(OUT)/runtime.asm
	$(LWASM) --6809 --format=raw -DSDC_NOVERCHK -o $(OUT)/dmpkg.nover.bin $(OUT)/runtime.asm
	@echo "runtime: $$(wc -c < $(OUT)/dmpkg.bin) bytes" \
	      "($$(wc -c < $(OUT)/dmpkg.nover.bin) with SDC_NOVERCHK), assembled at" '$(ASM_ORG)'

test: verify asm
	$(PYTHON) test/run_test.py

clean:
	rm -rf $(BUILD) $(OUT) $(EXAMPLE)/assets
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
