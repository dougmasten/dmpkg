# dmpkg tests for a real CoCo SDC (docs/HWTEST.md), one folder each:
#
#   main/     the hardware test: HWTEST.DSK, HWBIG.DSK, HWTEST.SDS
#   stress/   the stress test: STRESS.DSK
#
#   make            build and verify both (same as make all)
#   make verify     check their images
#   make clean      clean both
#
# Each folder's own Makefile works on its own too: cd main && make.

TESTS := main stress

.PHONY: all verify clean $(TESTS)
all: $(TESTS)

$(TESTS):
	$(MAKE) -C $@

verify clean:
	@for d in $(TESTS); do $(MAKE) -C $$d $@ || exit 1; done
