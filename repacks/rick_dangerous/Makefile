# Rick Dangerous as one dmpkg disk image (build/RICK.DSK)
#
#   make all        extract, assets, include files, loader, then the disk
#   make extract    copy the game files off bin/RICK1-3.DSK into extracted/
#   make verify     check the image's size, CRCs and build ID
#   make list       print the image's TOC
#   make clean
#   make help       list targets and variables (default)
#
# Input: only the original disks in bin/ (RICK1-3.DSK).  Needs lwasm and dmpkg.
# DMPKG points at the dmpkg repo; its packer runs from python/src.

DMPKG   ?= $(abspath ../..)
PYTHON  ?= python3
LWASM   ?= lwasm
LWFLAGS ?=
export PYTHONPATH := $(DMPKG)/python/src$(if $(PYTHONPATH),:$(PYTHONPATH))

B       := build
IMAGE   := $(B)/RICK.DSK
LOADER  := $(B)/RICK.BIN
DISKS   := bin/RICK1.DSK bin/RICK2.DSK bin/RICK3.DSK
EXTRACT := extracted/.stamp
ASSETS  := assets/.stamp
PASS1   := $(B)/.pass1
PACKER  := $(wildcard $(DMPKG)/python/src/dmpkg/*.py)

.PHONY: all extract verify list clean help FORCE
.DEFAULT_GOAL := help
all: $(IMAGE)
# rebuild the loader when LWFLAGS changes
FLAGS   := $(B)/.lwflags
$(FLAGS): FORCE
	@mkdir -p $(B)
	@echo '$(LWFLAGS)' | cmp -s - $@ || echo '$(LWFLAGS)' > $@
extract: $(EXTRACT)

# game files off the original disks (SPLASH0-3, COMP0000-0049)
$(EXTRACT): extract.py $(DISKS)
	$(PYTHON) extract.py
	@touch $@

$(ASSETS): mkassets.py $(EXTRACT)
	$(PYTHON) mkassets.py
	@touch $@

project.yaml: $(ASSETS)

# two passes: the loader embeds the build_id and descriptors
$(PASS1): $(ASSETS) project.yaml $(PACKER)
	$(PYTHON) -m dmpkg build project.yaml --no-loader
	@touch $@

$(LOADER): loader.asm $(DMPKG)/asm/dmpkg.asm $(PASS1) $(FLAGS)
	$(LWASM) --6809 --format=decb $(LWFLAGS) -I $(DMPKG)/asm -I $(B) --list=$(B)/RICK.lst -o $@ loader.asm

$(IMAGE): $(PASS1) $(LOADER)
	$(PYTHON) -m dmpkg build project.yaml -v

verify: $(IMAGE)
	$(PYTHON) -m dmpkg verify $(IMAGE)

list: $(IMAGE)
	$(PYTHON) -m dmpkg list $(IMAGE)

clean:
	rm -rf $(B) extracted assets project.yaml

help:
	@echo 'Targets:'
	@echo '  all      extract, assets, include files, loader, then $(IMAGE)'
	@echo '  extract  copy the game files from bin/*.DSK to extracted/'
	@echo '  verify   check the image'"'"'s size, CRCs and build ID'
	@echo '  list     print the image'"'"'s TOC'
	@echo '  clean    remove $(B)/, extracted/, assets/ and project.yaml'
	@echo '  help     show this message (default)'
	@echo ''
	@echo 'Variables (override with make VAR=value):'
	@echo '  DMPKG  = $(DMPKG)'
	@echo '  PYTHON = $(PYTHON)'
	@echo '  LWASM  = $(LWASM)'
	@echo '  LWFLAGS= $(LWFLAGS)  (e.g. -DDMPKG_DELAY=64)'
