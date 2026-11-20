# dmpkg stress test (docs/HWTEST.md#stress-test): STRESS.DSK
#
#   make            build STRESS.DSK and verify it (same as make all)
#   make verify     check the image's size, CRCs and build ID
#   make clean      remove build/ and assets/
#   make help       list targets and variables
#
# mkstress.py writes the 64 pattern assets and their checksum table, then the
# disk is built in two passes: the include files, STRESS.BIN assembled
# against them, then the disk with it. Needs python3 and lwasm. DMPKG points
# at the dmpkg repo (by default the one this folder is in).

DMPKG   ?= $(abspath ../..)
PYTHON  ?= python3
LWASM   ?= lwasm
LWFLAGS ?=
export PYTHONPATH := $(DMPKG)/python/src$(if $(PYTHONPATH),:$(PYTHONPATH))

B       := build
STAMP   := $(B)/.assets
PASS1   := $(B)/.pass1
IMAGE   := $(B)/STRESS.DSK
LOADER  := $(B)/STRESS.BIN
RUNTIME := $(DMPKG)/asm/dmpkg.asm
PACKER  := $(wildcard $(DMPKG)/python/src/dmpkg/*.py)

.PHONY: all verify clean help
.DEFAULT_GOAL := all
all: verify
	@echo "copy $(IMAGE) to the SD card and mount it on its own (docs/HWTEST.md#stress-test)"

$(STAMP): mkstress.py
	$(PYTHON) mkstress.py
	@touch $@

$(PASS1): $(STAMP) stress.yaml $(PACKER)
	$(PYTHON) -m dmpkg build stress.yaml --no-loader
	@touch $@

$(LOADER): stress.asm $(RUNTIME) $(PASS1)
	$(LWASM) --6809 --format=decb $(LWFLAGS) -I $(DMPKG)/asm -I $(B) -o $@ $<

$(IMAGE): $(LOADER)
	$(PYTHON) -m dmpkg build stress.yaml

verify: $(IMAGE)
	$(PYTHON) -m dmpkg verify $(IMAGE)

clean:
	rm -rf $(B) assets __pycache__

help:
	@echo 'Targets:'
	@echo '  all      build $(IMAGE) and verify it (default)'
	@echo '  verify   check the image'"'"'s size, CRCs and build ID'
	@echo '  clean    remove $(B)/ and assets/'
	@echo '  help     show this message'
	@echo ''
	@echo 'Variables (override with make VAR=value):'
	@echo '  DMPKG  = $(DMPKG)'
	@echo '  PYTHON = $(PYTHON)'
	@echo '  LWASM  = $(LWASM)'
	@echo '  LWFLAGS= $(LWFLAGS)  (e.g. -DDMPKG_DELAY=64)'
