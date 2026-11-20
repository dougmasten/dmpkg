"""CoCo BASIC programs: tokenize ASCII text the way the CoCo 3 ROM does, and
list tokenized programs.

The tokenizer follows the ROM's crunch routine (Color BASIC LB821, with the
Super Extended BASIC patch at LB8D4), per William Astle's disassemblies:

  - keywords are tried at each position in table order, all commands first
    (Color, Extended, Disk, Super Extended), then all functions; the first
    match wins, so it's prefix order, not longest match
  - commands are one byte ($80-$F8); functions are $FF then a byte
    ($80-$AD). Super Extended commands start at $E2 and its functions at
    $A9 whatever comes before them ($A8 stays unused)
  - after a letter that starts no keyword (a variable name), the letters
    and digits that follow are copied without looking for keywords
  - ? is PRINT; ' is stored as : then the ' token; ELSE gets a : in front
  - text in quotes, after REM or ', and after DATA up to the next : is
    copied as typed; digits, : and ; are never the start of a keyword
  - keywords are upper case only; spaces are kept

A program file is $FF, a 2-byte length, then the lines (next-line address,
line number, tokens, $00) and $00 $00. The next-line addresses are where the
lines would sit with the program at `base`: $2601 is Disk BASIC's start with
the usual 4 graphics pages (PCLEAR 4), as a SAVE from a stock CoCo has them.
"""
from __future__ import annotations

from .errors import PackError

COLOR_CMDS = ("FOR GO REM ' ELSE IF DATA PRINT ON INPUT END NEXT DIM READ RUN RESTORE "
              "RETURN STOP POKE CONT LIST CLEAR NEW CLOAD CSAVE OPEN CLOSE LLIST SET RESET "
              "CLS MOTOR SOUND AUDIO EXEC SKIPF TAB( TO SUB THEN NOT STEP OFF + - * / ^ AND "
              "OR > = <").split()
COLOR_FUNCS = ("SGN INT ABS USR RND SIN PEEK LEN STR$ VAL ASC CHR$ EOF JOYSTK LEFT$ "
               "RIGHT$ MID$ POINT INKEY$ MEM").split()
EXT_CMDS = ("DEL EDIT TRON TROFF DEF LET LINE PCLS PSET PRESET SCREEN PCLEAR COLOR "
            "CIRCLE PAINT GET PUT DRAW PCOPY PMODE PLAY DLOAD RENUM FN USING").split()
EXT_FUNCS = "ATN COS TAN EXP FIX LOG POS SQR HEX$ VARPTR INSTR TIMER PPOINT STRING$".split()
DISK_CMDS = ("DIR DRIVE FIELD FILES KILL LOAD LSET MERGE RENAME RSET SAVE WRITE VERIFY "
             "UNLOAD DSKINI BACKUP COPY DSKI$ DSKO$ DOS").split()      # Disk BASIC 1.1
DISK_FUNCS = "CVN FREE LOC LOF MKN$ AS".split()
SE_CMDS = ("WIDTH PALETTE HSCREEN LPOKE HCLS HCOLOR HPAINT HCIRCLE HLINE HGET HPUT "
           "HBUFF HPRINT ERR BRK LOCATE HSTAT HSET HRESET HDRAW CMP RGB ATTR").split()
SE_FUNCS = "LPEEK BUTTON HPOINT ERNO ERLIN".split()


def _numbered(words, first):
    return [(w, first + i) for i, w in enumerate(words)]


# (keyword, token) in the order the ROM tries them
COMMANDS = _numbered(COLOR_CMDS + EXT_CMDS + DISK_CMDS, 0x80) + _numbered(SE_CMDS, 0xE2)
FUNCTIONS = _numbered(COLOR_FUNCS + EXT_FUNCS + DISK_FUNCS, 0x80) + _numbered(SE_FUNCS, 0xA9)
CMD_NAME = {t: w for w, t in COMMANDS}
FUNC_NAME = {t: w for w, t in FUNCTIONS}

T_REM, T_QUOTE, T_ELSE, T_DATA, T_PRINT = 0x82, 0x83, 0x84, 0x86, 0x87
BASE = 0x2601
MAX_LINE = 63999


def crunch(text: str, upper: bool = False) -> bytes:
    """One line's text (after the line number) -> its tokens, without the
    terminating $00. upper: read a-z as A-Z, except in the text kept as typed
    (quotes, REM, ' and DATA)."""
    src = text.encode("latin-1")
    key = src.upper() if upper else src             # what keywords and names see
    out = bytearray()
    i, n = 0, len(src)
    alpha = data = False
    while i < n:
        c = key[i]
        i += 1
        if alpha:                                   # inside a variable name
            if 0x41 <= c <= 0x5A or 0x30 <= c <= 0x39:
                out.append(c)
                continue
            alpha = False
        if c == 0x20:                               # space: kept
            out.append(c)
            continue
        if c == 0x22:                               # "...": copied to the next "
            out.append(c)
            j = src.find(b'"', i)
            j = n if j < 0 else j + 1
            out += src[i:j]
            i = j
            continue
        if data or 0x30 <= c <= 0x3B:               # DATA text; digits, : and ;
            out.append(src[i - 1])
            if c == 0x3A:
                data = False                        # : ends DATA (and a name)
            continue
        if c == 0x3F:                               # ? = PRINT
            out.append(T_PRINT)
            continue
        if c == 0x27:                               # ' = : REM, rest as typed
            out += bytes([0x3A, T_QUOTE]) + src[i:]
            break
        tok = _match(key, i - 1)
        if tok is None:                             # not a keyword: copy one
            out.append(c)
            alpha = 0x41 <= c <= 0x5A
            continue
        word, fn, t = tok
        i += len(word) - 1
        if fn:
            out += bytes([0xFF, t])
        elif t == T_ELSE:
            out += bytes([0x3A, T_ELSE])
        else:
            out.append(t)
            if t == T_DATA:
                data = True
            elif t == T_REM:
                out += src[i:]
                break
    return bytes(out)


def _match(src: bytes, i: int):
    """(word, is_function, token) of the first keyword at src[i:], or None."""
    for table, fn in ((COMMANDS, False), (FUNCTIONS, True)):
        for word, t in table:
            if src.startswith(word.encode(), i):
                return word, fn, t
    return None


def parse_lines(text: str) -> dict[int, str]:
    """ASCII program -> {line number: text}, the way typing it in would:
    lines kept in number order, a later line replacing an earlier one with
    the same number, a number on its own deleting the line. Spaces between
    the number and the text are dropped."""
    lines: dict[int, str] = {}
    for k, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        s = raw.lstrip(" ")
        if not s:
            continue
        j = 0
        while j < len(s) and s[j] in "0123456789 ":
            j += 1
        digits = s[:j].replace(" ", "")
        if not digits:
            raise PackError(f"BASIC line {k}: no line number: {raw!r}")
        num = int(digits)
        if num > MAX_LINE:
            raise PackError(f"BASIC line {k}: line number {num} is over {MAX_LINE}")
        body = s[j:]
        if body:
            lines[num] = body
        else:
            lines.pop(num, None)
    return dict(sorted(lines.items()))


def tokenize(text: str, base: int = BASE, upper: bool = False) -> bytes:
    """ASCII program text -> a tokenized program file ($FF, length, lines).
    upper: upper-case the program outside quotes, REM, ' and DATA first."""
    prog = bytearray()
    for num, body in parse_lines(text).items():
        toks = crunch(body, upper)
        if len(toks) > 249:
            raise PackError(f"BASIC line {num} is too long ({len(toks)} bytes tokenized)")
        nxt = base + len(prog) + 4 + len(toks) + 1
        prog += nxt.to_bytes(2, "big") + num.to_bytes(2, "big") + toks + b"\0"
    prog += b"\0\0"
    return b"\xff" + len(prog).to_bytes(2, "big") + bytes(prog)


def lowercase_lines(text: str) -> list[int]:
    """Line numbers with lower-case letters where the ROM would look for
    keywords and names: outside quotes, REM, ' and DATA. Keywords are upper
    case only, so such a line stops with ?SN ERROR when it runs."""
    bad = []
    for num, body in parse_lines(text).items():
        quoted = data = False
        for c in crunch(body):
            if c == 0x22:
                quoted = not quoted
            elif quoted:
                continue
            elif c in (T_REM, T_QUOTE):
                break
            elif c == T_DATA:
                data = True
            elif c == 0x3A:
                data = False
            elif not data and 0x61 <= c <= 0x7A:
                bad.append(num)
                break
    return bad


def detokenize(data: bytes) -> str:
    """A tokenized program file (or just its lines) -> its listing, one
    line per program line, as LIST shows it."""
    i = 3 if data[:1] == b"\xff" else 0
    out = []
    while i + 4 <= len(data):
        if data[i] == 0 and data[i + 1] == 0:
            break
        num = data[i + 2] << 8 | data[i + 3]
        i += 4
        line = []
        quoted = False
        while i < len(data) and data[i]:
            c = data[i]
            i += 1
            if quoted or c < 0x80:
                if c == 0x22:
                    quoted = not quoted
                if c == 0x3A and not quoted and i < len(data) and data[i] in (T_QUOTE, T_ELSE):
                    continue                        # the : the tokenizer added
                line.append(chr(c))
            elif c == 0xFF:
                t = data[i]
                i += 1
                line.append(FUNC_NAME.get(t, f"[FF{t:02X}]"))
            else:
                line.append(CMD_NAME.get(c, f"[{c:02X}]"))
                if c in (T_REM, T_QUOTE):           # rest of the line as typed
                    while i < len(data) and data[i]:
                        line.append(chr(data[i]))
                        i += 1
        i += 1
        out.append(f"{num} {''.join(line)}")
    return "\n".join(out) + ("\n" if out else "")
