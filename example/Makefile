# dmpkg example: one DECB disk holding the loader and its assets (build/DEMO.DSK)
#
#   make            same as make build
#   make build      generate the assets, build the include files, assemble the
#                   loader, then build the disk image with it
#   make verify     check the image's size, CRCs and build ID
#   make list       print the image's TOC
#   make clean      remove build/ and assets/
#   make help       list targets and variables
#
# Needs python3 and lwasm. DMPKG points at the dmpkg repo (by default the one
# this folder is in); the packer runs from its python/src without installing.

DMPKG   ?= $(abspath ..)
PYTHON  ?= python3
LWASM   ?= lwasm
LWFLAGS ?=
export PYTHONPATH := $(DMPKG)/python/src$(if $(PYTHONPATH),:$(PYTHONPATH))

B       := build
IMAGE   := $(B)/DEMO.DSK
LOADER  := $(B)/LOADER.BIN
PASS1   := $(B)/.pass1
ASSETS  := assets/.stamp
RUNTIME := $(DMPKG)/asm/dmpkg.asm
PACKER  := $(wildcard $(DMPKG)/python/src/dmpkg/*.py)

.PHONY: all build verify list clean help
.DEFAULT_GOAL := build
all: build
build: $(IMAGE)

$(ASSETS): mkassets.py
	$(PYTHON) mkassets.py
	@touch $@

# Two passes: the loader embeds the build_id and descriptors from the include
# files, so they're written first (pass 1), then the loader is assembled and
# the image rebuilt with it on the disk. Asset placement and build_id don't
# depend on the loader, so pass 2 writes the same include files.
$(PASS1): $(PACKER) project.yaml $(ASSETS)
	$(PYTHON) -m dmpkg build project.yaml --no-loader
	@touch $@

$(LOADER): loader.asm $(RUNTIME) $(PASS1)
	$(LWASM) --6809 --format=decb $(LWFLAGS) -I $(DMPKG)/asm -I $(B) -o $@ loader.asm

$(IMAGE): $(PASS1) $(LOADER)
	$(PYTHON) -m dmpkg build project.yaml -v

verify: $(IMAGE)
	$(PYTHON) -m dmpkg verify $(IMAGE)

list: $(IMAGE)
	$(PYTHON) -m dmpkg list $(IMAGE)

clean:
	rm -rf $(B) assets __pycache__

help:
	@echo 'Targets:'
	@echo '  build    assets, include files, loader, then $(IMAGE) (default)'
	@echo '  verify   check the image'"'"'s size, CRCs and build ID'
	@echo '  list     print the image'"'"'s TOC'
	@echo '  clean    remove $(B)/ and assets/'
	@echo '  help     show this message'
	@echo ''
	@echo 'Variables (override with make VAR=value):'
	@echo '  DMPKG  = $(DMPKG)'
	@echo '  PYTHON = $(PYTHON)'
	@echo '  LWASM  = $(LWASM)'
	@echo '  LWFLAGS= $(LWFLAGS)'
