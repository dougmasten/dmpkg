#!/usr/bin/env python3
"""
The BASIC tokenizer: lines against the bytes a real CoCo saved them as, one
case for each of the ROM's tokenizing rules, the program file format and
line links, listing back, and AUTOEXEC.BAS tokenized or ASCII.
"""
import sys

from harness import *  # noqa: F401,F403  (the model, the machine, helpers)

# Lines from a tokenized program saved on a real CoCo (ROBOCOP.BAS): text as
# LIST shows it, then the bytes on the disk
BASIC_REAL = [
    ('LINEINPUT"START AT WHICH LEVEL? (1-6) ";A$:IF A$="" THEN 1810',
     "bb89225354415254204154205748494348204c4556454c3f2028312d362920223b41243a85204124"
     "b3222220a72031383130"),
    ("POKE &H42EC,VV:'**POKE THE STARTING LEVEL",
     "92202648343245432c56563a3a832a2a504f4b4520544845205354415254494e47204c4556454c"),
    ("FORX=1TO8:READA:POKEA,&HBD:POKEA+1,&H5E:POKEA+2,&HB3:NEXT",
     "8058b331a5383a8d413a92412c264842443a9241ab312c264835453a9241ab322c264842333a8b"),
    ("POKE&H45C9,&HDF:POKE&H4B3D,&HDF:POKE&H7B03,&HDF:POKE&H4037,&H3F:POKE&H4038,&HBE",
     "922648343543392c264844463a922648344233442c264844463a922648374230332c264844463a92"
     "2648343033372c264833463a922648343033382c26484245"),
]
# The ROM's rules, one case each (bytes worked out from the crunch routine)
BASIC_RULES = [
    ("FORT=1TO10", "8054b331a53130", "keyword at the start of a name: FOR T"),
    ("SCORE=ATOM", "53434f5245b341544f4d", "inside a name: no keywords (no OR, no TO)"),
    ('?"A";:\'X PRINT', "872241223b3a3a8358205052494e54", "? is PRINT, ' is :REM, rest as typed"),
    ('DATA 1,FOR,"A:B",X:PRINT', "862031 2c464f522c22413a42222c583a87",
     "DATA text as typed up to the :, quotes kept whole"),
    ("LOCATE 1,2:X=LOC(1)", "f12031 2c323a58b3ffa4283129", "commands before functions: LOCATE, LOC"),
    ("H=LPEEK(0):E=ERNO", "48b3ffa92830293a45b3ffac", "Super Extended functions start at $A9"),
    ("WIDTH 40:ATTR 1,0", "e220 34303af82031 2c30", "Super Extended commands start at $E2"),
    ("IF A THEN 1 ELSE 2", "852041 20a720312 03a842032", "ELSE gets a : in front"),
    ('print "x"', "7072696e742022782 2", "lower case is not a keyword"),
    ("REM IF THEN", "82204946205448454e", "REM: rest as typed"),
    ("A=B AND C OR NOT D>=E", "41b34220b0204320b120a82044b2b345", "operators are tokens"),
]


def basic_test():
    """The BASIC tokenizer against lines tokenized by a real CoCo, the ROM's
    rules, the program file format, and listing back."""
    results = []
    bad = [t for t, h in BASIC_REAL if basic.crunch(t) != bytes.fromhex(h)]
    results.append(check(not bad, f"{len(BASIC_REAL)} lines tokenized as a real CoCo did"
                         + (f": {bad[0]!r} differs" if bad else "")))
    for text, h, what in BASIC_RULES:
        got = basic.crunch(text)
        results.append(check(got == bytes.fromhex(h.replace(" ", "")),
                             f"tokenize: {what}" + ("" if got == bytes.fromhex(h.replace(" ", ""))
                                                    else f" (got {got.hex()})")))
    prog = basic.tokenize("20 B\n10 A\n30 C\n20  X\n30\n")
    results.append(check(prog == b"\xff\x00\x0e\x26\x07\x00\x0aA\x00\x26\x0d\x00\x14X\x00\x00\x00",
                         "program file: lines in order, a repeat replaces, a bare number deletes, "
                         "links for $2601"))
    results.append(check(basic.tokenize("10 A", base=0x0E01)[3:5] == b"\x0e\x07",
                         "program file: links for another base"))
    text = "".join(f"10 {t}\n".replace("10 ", f"{10 * (i + 1)} ", 1)
                   for i, (t, _) in enumerate(BASIC_REAL))
    results.append(check(basic.detokenize(basic.tokenize(text)) == text,
                         "detokenize lists the lines back as typed (: before ' and ELSE hidden)"))
    results.append(check(decb.autoexec("LOADER", "BIN") == ("AUTOEXEC", "BAS", AUTOEXEC_TOK)
                         and decb.autoexec("LOADER", "BIN", False)[2]
                         == b'10 LOADM"LOADER.BIN":EXEC\r',
                         "AUTOEXEC.BAS tokenized, or ASCII with disk.tokenize: false"))
    return all(results)


def run():
    return basic_test()

if __name__ == "__main__":
    sys.exit(0 if run() else 1)
