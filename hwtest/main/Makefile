# dmpkg hardware test (docs/HWTEST.md): HWTEST.DSK, HWBIG.DSK and HWTEST.SDS
#
#   make            build the three images and verify them (same as make all)
#   make verify     check the images' sizes, CRCs and build IDs
#   make clean      remove build/ and assets/
#   make help       list targets and variables
#
# HWTEST.SDS is mounted by name, and the two disks' loaders include its
# symbols, so it's built first. Each disk is then built in two passes: the
# include files, the loader assembled against them, then the disk with it.
# Needs python3 and lwasm. DMPKG points at the dmpkg repo (by default the one
# this folder is in); the packer runs from its python/src without installing.

DMPKG   ?= $(abspath ../..)
PYTHON  ?= python3
LWASM   ?= lwasm
LWFLAGS ?=
export PYTHONPATH := $(DMPKG)/python/src$(if $(PYTHONPATH),:$(PYTHONPATH))

B       := build
STAMP   := $(B)/.assets
SDS     := $(B)/HWTEST.SDS
DISKS   := $(B)/HWTEST.DSK $(B)/HWBIG.DSK
RUNTIME := $(DMPKG)/asm/dmpkg.asm
PACKER  := $(wildcard $(DMPKG)/python/src/dmpkg/*.py)

.PHONY: all verify clean help
.DEFAULT_GOAL := all
all: verify
	@echo "copy $(SDS) $(DISKS) to one folder on the SD card (docs/HWTEST.md)"

$(STAMP): mkhw.py disk.yaml big.yaml stream.yaml $(PACKER)
	$(PYTHON) mkhw.py
	@touch $@

$(SDS): $(STAMP)
	$(PYTHON) -m dmpkg build stream.yaml

$(B)/.pass1-%: $(STAMP)
	$(PYTHON) -m dmpkg build $*.yaml --no-loader
	@touch $@

$(B)/HWTEST.BIN: hwtest.asm $(RUNTIME) $(SDS) $(B)/.pass1-disk
	$(LWASM) --6809 --format=decb $(LWFLAGS) -I $(DMPKG)/asm -I $(B)/disk -I $(B) -o $@ $<

$(B)/HWBIG.BIN: hwtest.asm $(RUNTIME) $(SDS) $(B)/.pass1-big
	$(LWASM) --6809 --format=decb -DHWBIG $(LWFLAGS) -I $(DMPKG)/asm -I $(B)/big -I $(B) -o $@ $<

$(B)/HWTEST.DSK: $(B)/HWTEST.BIN
	$(PYTHON) -m dmpkg build disk.yaml

$(B)/HWBIG.DSK: $(B)/HWBIG.BIN
	$(PYTHON) -m dmpkg build big.yaml

verify: $(SDS) $(DISKS)
	@for f in $(SDS) $(DISKS); do $(PYTHON) -m dmpkg verify $$f || exit 1; done

clean:
	rm -rf $(B) assets __pycache__

help:
	@echo 'Targets:'
	@echo '  all      build $(SDS) $(DISKS) and verify them (default)'
	@echo '  verify   check the images'"'"' sizes, CRCs and build IDs'
	@echo '  clean    remove $(B)/ and assets/'
	@echo '  help     show this message'
	@echo ''
	@echo 'Variables (override with make VAR=value):'
	@echo '  DMPKG  = $(DMPKG)'
	@echo '  PYTHON = $(PYTHON)'
	@echo '  LWASM  = $(LWASM)'
	@echo '  LWFLAGS= $(LWFLAGS)  (e.g. -DDMPKG_DELAY=64)'
