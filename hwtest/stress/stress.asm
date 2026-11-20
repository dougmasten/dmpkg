;-------------------------------------------------------------------
; stress.asm - dmpkg stress test for a real CoCo SDC
;
; Lives on STRESS.DSK as STRESS.BIN; AUTOEXEC.BAS runs it, or type
; LOADM"STRESS":EXEC. Each pass opens the disk it came from (so the header
; and build_id check runs every pass too), streams its 64 assets of 8K
; (512K) one after another into $4000-$5FFF, and checks each one's Fletcher
; sums against the table mkstress.py wrote. Asset k holds a rolling pattern
; (byte i = i + i/256 + 37k), so after a bad sum the loader finds the first
; wrong byte and shows where it was, what it read and what it wanted.
; Errors from dmpkg_open or dmpkg_load are counted by code, and the pass
; starts over. It runs until a key is pressed, then returns to BASIC.
;
; Works on any CoCo with 32K or more; for 1.79 MHz on a CoCo 3, POKE 65497,0
; first. Needs a CoCo SDC with firmware 117 or later. See docs/HWTEST.md.
;
;   make (hwtest/stress/Makefile), or:
;   lwasm --6809 --format=decb -I <dmpkg>/asm -I build -o build/STRESS.BIN stress.asm
;-------------------------------------------------------------------

POLCAT  equ     $A000           ; ROM: A = key, Z set if none
CURPOS  equ     $0088           ; BASIC's cursor
TEXT    equ     $0400           ; 32x16 text screen
BUF     equ     $4000           ; every asset loads here

        org     $1000           ; below AUTOEXEC.BAS ($2601)
start   pshs    cc
        orcc    #$50            ; Disk BASIC's motor-off timer would drop
                                ; the SDC out of command mode
        ldx     #vars           ; counters to 0
@clr    clr     ,x+
        cmpx    #varend
        blo     @clr
        lbsr    labels          ; clear the screen, put up the labels
        jsr     dmpkg_init      ; SDC there, firmware 117 or later
        bcc     pass
        ldb     #$FE            ; error at init
        lbsr    error
        lbsr    show
        lbra    quit

; ---- one pass: open, then every asset in image order (one stream)
pass    ldx     #DMPKG_MountName ; 0: the disk already in drive 0
        ldy     #DMPKG_BuildID
        jsr     dmpkg_open
        bcc     @open
        ldb     #$FF            ; error at open
        lbsr    error
        bra     endpass
@open   clr     k
        ldx     #S00
        stx     desc
@asset  ldx     desc
        jsr     dmpkg_load
        bcc     @loaded
        ldb     k               ; error loading asset k
        lbsr    error
        bra     endpass
@loaded lbsr    check           ; sums against the table, find a bad byte
        jsr     [POLCAT]        ; a key stops the test
        bne     stop
        ldx     desc
        leax    DMPKG_DESC_SIZE,x
        stx     desc
        inc     k
        lda     k
        cmpa    #STRESS_N
        blo     @asset
        ldd     passes          ; a whole pass
        addd    #1
        std     passes
endpass jsr     dmpkg_close
        lbsr    show
        jsr     [POLCAT]
        beq     pass
stop    jsr     dmpkg_close
        lbsr    show
quit    ldx     #TEXT+15*32     ; BASIC's OK on the bottom line
        stx     CURPOS
        puls    cc,pc

; error: A = dmpkg error code, B = asset, $FF at open or $FE at init.
; Counts it by where and by code, and keeps it as the last error.
error   sta     ecode
        stb     ewhere
        ldx     #operr          ; open (and init) errors
        cmpb    #STRESS_N
        bhs     @where
        ldx     #lderr          ; load errors
@where  lbsr    inc16
        ldx     #codes
        lda     ecode           ; (inc16 used A)
        anda    #7
        lsla
        leax    a,x
        bra     inc16

; inc16: the 16-bit counter at X plus 1
inc16   ldd     ,x
        addd    #1
        std     ,x
        rts

; check: Fletcher sums of the asset at BUF against StressSums[k]. On a
; mismatch, count it and find the first byte off the pattern.
check   ldx     #BUF
        clra                    ; D = sum1
        clrb
        ldu     #0              ; U = sum2
@sum    addb    ,x+
        adca    #0
        leau    d,u
        addb    ,x+
        adca    #0
        leau    d,u
        addb    ,x+
        adca    #0
        leau    d,u
        addb    ,x+
        adca    #0
        leau    d,u
        cmpx    #BUF+STRESS_SIZE
        blo     @sum
        pshs    d
        ldb     k               ; Y = StressSums + 4k
        clra
        lslb
        rola
        lslb
        rola
        ldy     #StressSums
        leay    d,y
        puls    d
        cmpd    ,y
        bne     @bad
        cmpu    2,y
        bne     @bad
        rts
@bad    ldx     #badsum
        lbsr    inc16
        lda     k
        sta     bk
        ldb     #STRESS_STEP    ; A = byte 0 of asset k: 37k
        mul
        tfr     b,a
        ldx     #BUF
@page   clrb                    ; 256 bytes a page
@byte   cmpa    ,x+
        bne     @found
        inca
        decb
        bne     @byte
        inca                    ; the i/256 term
        cmpx    #BUF+STRESS_SIZE
        blo     @page
        ldd     #$FFFF          ; sums off but every byte right
        std     boff
        rts
@found  sta     bwant
        lda     -1,x
        sta     bgot
        tfr     x,d
        subd    #BUF+1
        std     boff
        rts

; ---- screen: VDG codes, written straight to the text screen

; labels: clear the screen and put up the fixed text
labels  ldx     #TEXT
        ldd     #$6060
@cls    std     ,x++
        cmpx    #TEXT+512
        blo     @cls
        ldy     #Labels
@next   ldx     ,y++            ; screen address, 0 at the end
        beq     @done
        lbsr    puts
        bra     @next
@done   rts

; puts: the zero-terminated string at Y to the screen at X (both advance)
puts    lda     ,y+
        beq     @done
        lbsr    putc
        bra     puts
@done   rts

; putc: ASCII in A to the screen at X+ as VDG (lower case shows inverse)
putc    cmpa    #$40
        blo     @flip
        cmpa    #$60
        blo     @store
        anda    #$DF
@flip   eora    #$40
@store  sta     ,x+
        rts

; show: the counters and the last bad sum and error
show    ldx     #TEXT+2*32+12
        ldd     passes
        lbsr    dec5
        ldx     #TEXT+3*32+12
        ldd     badsum
        lbsr    dec5
        ldx     #TEXT+4*32+12
        ldd     operr
        lbsr    dec5
        ldx     #TEXT+5*32+12
        ldd     lderr
        lbsr    dec5
        ldy     #codes+2        ; E1-E7 at the places in CodePos
        ldu     #CodePos
@code   ldx     ,u++
        ldd     ,y++
        lbsr    dec5
        cmpu    #CodePos+14
        blo     @code
        ldd     badsum          ; last bad sum
        beq     @noerr
        ldx     #TEXT+11*32+6
        lda     bk
        lbsr    dec2
        ldx     #TEXT+11*32+14
        ldd     boff
        lbsr    hex4
        ldx     #TEXT+12*32+5
        lda     bgot
        lbsr    hex2
        ldx     #TEXT+12*32+15
        lda     bwant
        lbsr    hex2
@noerr  lda     ecode           ; last error
        beq     @done
        ldx     #TEXT+14*32+12
        lda     #'E
        lbsr    putc
        lda     ecode
        adda    #'0
        lbsr    putc
        leax    1,x
        ldb     ewhere
        ldy     #MsgInit
        cmpb    #$FE
        beq     @what
        ldy     #MsgOpen
        cmpb    #$FF
        beq     @what
        ldy     #MsgAsset
        lbsr    puts
        lda     ewhere
        bra     dec2
@what   lbra    puts
@done   rts

; dec5: D as 5 decimal digits at X+, leading zeros as spaces
dec5    pshs    y
        ldy     #Pow10
        clr     ,-s             ; nonzero once a digit has shown
@digit  pshs    a
        lda     #'0-1
        sta     ,-s             ; digit
        lda     1,s
@sub    inc     ,s
        subd    ,y
        bcc     @sub
        addd    ,y              ; one too many
        sta     1,s
        lda     ,s+             ; the digit
        cmpy    #Pow10+8        ; always show the units
        beq     @show
        cmpa    #'0
        bne     @show
        tst     1,s
        bne     @show
        lda     #$20            ; space
        bra     @put
@show   inc     1,s
@put    pshs    b
        lbsr    putc
        puls    b
        puls    a
        leay    2,y
        cmpy    #Pow10+10
        blo     @digit
        leas    1,s
        puls    y,pc

; dec2: A (0-99) as 2 decimal digits at X+
dec2    clrb
@tens   cmpa    #10
        blo     @ones
        suba    #10
        incb
        bra     @tens
@ones   pshs    a
        tfr     b,a
        adda    #'0
        lbsr    putc
        puls    a
        adda    #'0
        lbra    putc

; hex4 / hex2: D or A as hex digits at X+
hex4    pshs    b
        lbsr    hex2
        puls    a
hex2    pshs    a
        lsra
        lsra
        lsra
        lsra
        lbsr    @nib
        puls    a
        anda    #$0F
@nib    adda    #'0
        cmpa    #'9
        bls     @out
        adda    #7
@out    lbra    putc

Pow10   fdb     10000,1000,100,10,1
CodePos fdb     TEXT+7*32+3,TEXT+7*32+13,TEXT+7*32+23 ; E1-E3
        fdb     TEXT+8*32+3,TEXT+8*32+13,TEXT+8*32+23 ; E4-E6
        fdb     TEXT+9*32+3                       ; E7

Labels  fdb     TEXT+0*32
        fcn     "DMPKG STRESS TEST  ANY KEY STOPS"
        fdb     TEXT+2*32
        fcn     "PASSES"
        fdb     TEXT+3*32
        fcn     "BAD SUM"
        fdb     TEXT+4*32
        fcn     "OPEN ERRORS"
        fdb     TEXT+5*32
        fcn     "LOAD ERRORS"
        fdb     TEXT+7*32
        fcn     "E1        E2        E3"
        fdb     TEXT+8*32
        fcn     "E4        E5        E6"
        fdb     TEXT+9*32
        fcn     "E7"
        fdb     TEXT+11*32
        fcn     "ASSET    OFF $"
        fdb     TEXT+12*32
        fcn     "GOT $    WANT $"
        fdb     TEXT+14*32
        fcn     "LAST ERROR"
        fdb     0
MsgInit fcn     "AT INIT"
MsgOpen fcn     "AT OPEN"
MsgAsset fcn    "AT ASSET "

; ---- counters (cleared at start)
vars
passes  rmb     2               ; whole passes
badsum  rmb     2               ; assets with bad sums
operr   rmb     2               ; errors at init or open
lderr   rmb     2               ; errors loading an asset
codes   rmb     16              ; errors by code, 0-7
ecode   rmb     1               ; last error: code, 0 for none
ewhere  rmb     1               ;   asset, $FF open, $FE init
bk      rmb     1               ; last bad sum: asset
boff    rmb     2               ;   offset of the first bad byte
bgot    rmb     1               ;   byte read
bwant   rmb     1               ;   byte wanted
k       rmb     1               ; asset being loaded
desc    rmb     2               ; its descriptor
varend

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
        include "ssums.inc"

; must end below $2600 (AUTOEXEC.BAS is at $2601): run_test.py checks it

        end     start
