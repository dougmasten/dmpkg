;-------------------------------------------------------------------
; loader.asm - Rick Dangerous loader for the single dmpkg disk image
;
; Lives on RICK.DSK as RICK.BIN; AUTOEXEC.BAS runs it at power-on, or
; type LOADM"RICK":EXEC. It replaces RICK.BAS, MOVER.BIN and CC3X0.BIN:
;
;   1. clears the screen and asks for the monitor type, as RICK.BAS did
;   2. streams the splash into pages $00-$03 and shows it, like MOVER
;   3. streams the decompressed game straight to its physical pages, the
;      bytes CC3X0 would have unpacked from COMP0000-0049.BIN
;   4. sets up what the original hand-over left behind, then enters the
;      game's Boot at $FD00 (page $3F):
;        - page $38 copied to page $3E (MOVER StartGame)
;        - task 1 = $38,$30,$31,$32,$33,$3D,$35,$3F (stream header, loaded
;          by CC3X0 LaunchGame; the FIRQ sound handlers run in task 1 and
;          rely on slots 0 and 7, which the game never sets)
;        - task 0 = $38-$3F, RAM mode, fast CPU, B = 1 for composite
;          (boot stub in page $FF)
;
; Requires a CoCo 3 with 2MB, a 6309 (the game checks) and a CoCo SDC
; with firmware 117 or later. On a load error it shows LOAD ERROR n
; (n = dmpkg DMPKG_E* code) and stops: by then BASIC is overwritten.
;
; Assemble after a first dmpkg build has written the include files:
;   lwasm --6809 --format=decb -I <dmpkg>/asm -I build -o build/RICK.BIN loader.asm
;-------------------------------------------------------------------

POLCAT  equ     $A000           ; ROM: A = key or Z set if none
CHROUT  equ     $A002           ; ROM: print A
CLS     equ     $A928           ; ROM: clear the text screen, cursor home

GIME_INIT0  equ $FF90
GIME_VMODE  equ $FF98
GIME_VRES   equ $FF99
GIME_BORDER equ $FF9A
GIME_VBANK  equ $FF9B
GIME_VSCROLL equ $FF9C
GIME_VOFF   equ $FF9D
GIME_HOFF   equ $FF9F
MMU_T0      equ $FFA0
MMU_T1      equ $FFA8
PALETTE     equ $FFB0
SAM_FAST    equ $FFD9
SAM_RAMMODE equ $FFDF

TEXTSCR equ     $0400           ; 32x16 text screen (page $38)
GAMEBOOT equ    $FD00           ; game Boot, page $3F

        org     $1000           ; page $38: the only one the game doesn't load
start
        ldx     #$0100          ; CPU check, as in the game's Boot, so a 6809
        fcb     $1F,$18         ; stops here, not after the whole load.
        tsta                    ; TFR X,A gives the high byte on a 6309 ($01),
        bne     @cpuok          ; the low byte on a 6809 ($00)
        ldx     #Msg6809
        lbsr    puts
        rts                     ; back to BASIC: nothing touched yet
@cpuok  jsr     CLS             ; RICK.BAS: CLS, banner, wait for C or R
        ldx     #Banner
        lbsr     puts
@key    jsr     [POLCAT]
        beq     @key
        anda    #$DF            ; upper case
        clrb                    ; B = 0: RGB
        cmpa    #'R
        beq     @got
        incb                    ; B = 1: composite
        cmpa    #'C
        bne     @key
@got    stb     Composite
        ldx     #MsgLoad
        lbsr    puts

        orcc    #$50            ; no interrupts from here on: Disk BASIC's
                                ; motor-off timer would drop the SDC out of
                                ; command mode, and BASIC is about to go
        sta     SAM_FAST        ; 1.79 MHz: the copy loop is CPU bound. Not
                                ; native mode: on real hardware the SDC can't
                                ; keep up with its data reads (LOAD ERROR 4,
                                ; even with DMPKG_DELAY=64)
        lds     #start         ; stack below the code: BASIC's is in page
                                ; $3B, which the game overwrites
        jsr     dmpkg_init      ; once: SDC there, firmware 117+
        lbcs    Error
        ldx     #DMPKG_MountName ; 0: the disk already in drive 0
        ldy     #DMPKG_BuildID
        jsr     dmpkg_open
        lbcs    Error
        ldx     #SPLASH         ; pages $00-$03
        jsr     dmpkg_load
        lbcs     Error
        bsr     GfxMode         ; show it while the game loads
        ldx     #SPLASH+DMPKG_DESC_SIZE
        ldy     #DMPKG_ASSET_COUNT-1
@load   jsr     dmpkg_load      ; keeps X and Y; in image order, so it's
        lbcs     Error           ; one stream with no restarts
        leax    DMPKG_DESC_SIZE,x
        leay    -1,y
        bne     @load
        jsr     dmpkg_close

; page $38 -> page $3E, as MOVER StartGame did
        lda     #$3E
        sta     MMU_T0+2        ; $3E at $4000
        ldx     #$0000
        ldu     #$4000
@copy   ldd     ,x++
        std     ,u++
        cmpx    #$2000
        bne     @copy

; both task maps, then into the game
        ldx     #Task1Map       ; Task0Map follows it
        ldu     #MMU_T1
@map1   lda     ,x+
        sta     ,u+
        cmpu    #MMU_T1+8
        bne     @map1
        ldu     #MMU_T0         ; $38 stays at $0000: this code
@map0   lda     ,x+
        sta     ,u+
        cmpu    #MMU_T0+8
        bne     @map0
        sta     SAM_RAMMODE
        ldb     Composite       ; Boot stores B in MonitorComposite
        jmp     GAMEBOOT        ; sets its own stack, DP, GIME, PIAs

; print the string at X (0-terminated) with the ROM
puts    lda     ,x+
        beq     @done
        jsr     [CHROUT]
        bra     puts
@done   rts

; GfxMode: 320x200x16 at physical 0 with the splash palette (MOVER $1274)
GfxMode ldd     #$4C80
        sta     GIME_INIT0
        stb     GIME_VMODE
        lda     #$7A
        sta     GIME_VRES
        clr     GIME_BORDER
        clr     GIME_VBANK
        clr     GIME_VSCROLL
        clra
        clrb
        std     GIME_VOFF
        clr     GIME_HOFF
        ldu     #PalRGB
        tst     Composite
        beq     setpal
        ldu     #PalComposite
setpal  ldx     #PALETTE
@loop   lda     ,u+
        sta     ,x+
        cmpx    #PALETTE+16
        bne     @loop
        rts

; Error: A = dmpkg error code. Back to the text screen (MOVER $127C),
; print LOAD ERROR n straight to it and stop.
Error   pshs    a
        jsr     dmpkg_close
        ldd     #$CC00
        sta     GIME_INIT0
        stb     GIME_VMODE
        clr     GIME_VRES
        clr     GIME_BORDER
        clr     GIME_VBANK
        lda     #$0F
        sta     GIME_VSCROLL
        ldd     #$E000
        std     GIME_VOFF
        clr     GIME_HOFF
        ldu     #PalText
        bsr     setpal
        ldx     #TEXTSCR
        ldd     #$6060          ; clear the screen (VDG space)
@cls    std     ,x++
        cmpx    #TEXTSCR+512
        bne     @cls
        ldx     #TEXTSCR+32*7+10
        ldu     #ErrMsg
@msg    lda     ,u+
        beq     @num
        anda    #$3F            ; ASCII -> VDG normal video
        ora     #$40
        sta     ,x+
        bra     @msg
@num    puls    a
        adda    #$70            ; '0' in VDG normal video
        sta     ,x
@hang   bra     @hang

Banner  fcc     "RICK DANGEROUS"
        fcb     13,13
        fcc     "COCO3 VERSION BY GLEN HEWLETT"
        fcb     13
        fcc     "VERSION 1.0"
        fcb     13,13
        fcc     "REQUIRES:"
        fcb     13
        fcc     "2 MEGS OF RAM AND A 6309 CPU"
        fcb     13,13
        fcc     "MONITOR TYPE:"
        fcb     13
        fcc     "PRESS C FOR COMPOSITE"
        fcb     13
        fcc     "PRESS R FOR RGB"
        fcb     13,0
MsgLoad fcc     "STARTING TO LOAD..."
        fcb     13,0
Msg6809 fcc     "THIS GAME REQUIRES A 6309 CPU"
        fcb     13,0
ErrMsg  fcn     "LOAD ERROR "

Composite fcb   0

Task1Map fcb    $38,$30,$31,$32,$33,$3D,$35,$3F
Task0Map fcb    $38,$39,$3A,$3B,$3C,$3D,$3E,$3F

; palettes from MOVER
PalRGB  fcb     $00,$24,$23,$35,$00,$0A,$0B,$02,$06,$04,$22,$26,$07,$07,$38,$38
PalComposite fcb $00,$16,$17,$35,$00,$1D,$1C,$01,$14,$05,$15,$26,$20,$20,$30,$30
PalText fcb     $12,$24,$0B,$07,$3F,$1F,$09,$26,$00,$12,$00,$3F,$00,$12,$00,$26

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"

        end     start
