;------------------------------------------------------------------------------
; hwtest.asm - dmpkg hardware test (docs/HWTEST.md)
;
; Lives on HWTEST.DSK as HWTEST.BIN (HWBIG.DSK as HWBIG.BIN with -DHWBIG);
; AUTOEXEC.BAS runs it. Every check prints a cell on the 32x16 text screen:
; OK, SKIP, BAD (bytes don't match their checksum), E0 (an error was
; expected and none came) or En (dmpkg error code n). Failures show in
; inverse video, and a summary line follows. Returns to BASIC with A = the
; number of failures.
;
;   SDC FW      sdc_init: presence check and firmware version
;   COCO        CoCo 3 (phys loads run) or CoCo 1/2 (they're skipped)
;   BAD ID      sdc_open on the mounted disk with a wrong build_id: E4
;   OPEN        sdc_open on the mounted disk (no mount)
;   STOP        three sdc_getbyte, then sdc_stop
;   A_ODD..     each asset: memory wiped, sdc_load, bytes checked
;   EXEC        call the decb asset's exec address: D = $600D
;   RELOAD B    B_PACK again, out of order: restart and skip to its offset
;   MOUNT SDS   sdc_open "M:HWTEST.SDS" by name, then its two assets
;   NO FILE     sdc_open "M:NOSUCH.SDS": E3
;   REMOUNT     sdc_open "M:HWTEST.DSK" (or HWBIG.DSK), so Disk BASIC gets
;               its disk back in drive 0
;
; Build: make hwtest (two passes, like the example).
;------------------------------------------------------------------------------

SCREEN          equ     $0400           ; 32x16 text screen
CURPOS          equ     $0088           ; BASIC's cursor address

; cell results (anything else is a dmpkg error code)
R_OK            equ     0
R_E0            equ     $80             ; expected an error, none came
R_BAD           equ     $FF             ; bytes don't match the checksum
R_SKIP          equ     $FE             ; not run
R_TEXT          equ     $FD             ; show the string at rtext
R_NONE          equ     $FC             ; label only

        org     $1000           ; above Disk BASIC's work area, below the
                                ; BASIC program at $2601 (AUTOEXEC.BAS)
start
        orcc    #$50            ; mask IRQ/FIRQ: Disk BASIC's motor-off
                                ; timer writes $FF40
        clr     fails
        ldx     #SCREEN
        stx     cursor
        ldd     #$6060          ; clear the screen
@cls    std     ,x++
        cmpx    #SCREEN+512
        blo     @cls
        ldx     #t_title
        jsr     text
        jsr     newrow
        ldx     #SDC_BuildID    ; badid = the real build_id inverted
        ldy     #badid
        ldb     #4
@inv    lda     ,x+
        coma
        sta     ,y+
        decb
        bne     @inv
;
; ---- controller and machine
;
        jsr     sdc_init
        bcc     @found
        ldx     #t_sdc
        jsr     cell            ; E1: no SDC, E6: no answer
        lbra    finish
@found  jsr     fmtver
        ldx     #vbuf
        stx     rtext
        lda     #R_TEXT
        ldx     #t_sdc
        jsr     cell
        ldx     #t_coco2
        ldd     $FFFE           ; reset vector: $8C1B on a CoCo 3
        cmpd    #$8C1B
        bne     @mach
        inc     coco3
        ldx     #t_coco3
@mach   lda     #R_NONE
        jsr     cell
;
; ---- the disk the loader came from, no mount
;
        ldx     #0
        ldy     #badid
        jsr     sdc_open
        ldb     #SDC_EHDR
        jsr     want
        ldx     #t_badid
        jsr     cell
        ldx     #SDC_MountName  ; 0: the disk in SDC_DRIVE
        ldy     #SDC_BuildID
        jsr     sdc_open
        jsr     got
        ldx     #t_open
        jsr     cell
        tsta
        lbne    finish
        jsr     sdc_getbyte     ; stop part way through a block
        bcs     @stop
        jsr     sdc_getbyte
        bcs     @stop
        jsr     sdc_getbyte
        bcs     @stop
        jsr     sdc_stop
@stop   jsr     got
        ldx     #t_stop
        jsr     cell
        ldx     #A_ODD          ; manifest order: E_CODE sits in D_ALIGN's
        ldy     #A_ODD_SUM      ; align gap, so E is a seek backwards
        ldu     #t_a
        jsr     ldtest
        ldx     #B_PACK
        ldy     #B_PACK_SUM
        ldu     #t_b
        jsr     ldtest
        ldx     #C_MULTI
        ldy     #C_MULTI_SUM
        ldu     #t_c
        jsr     ldtest
        ldx     #D_ALIGN
        ldy     #D_ALIGN_SUM
        ldu     #t_d
        jsr     ldtest
        ldx     #E_CODE
        ldy     #E_CODE_SUM
        ldu     #t_e
        jsr     ldtest
        lda     #R_SKIP         ; run the decb asset's code
        tst     lres
        bne     @exec
        jsr     [lastu]
        cmpd    #$600D
        lbsr    same
@exec   ldx     #t_exec
        jsr     cell
        lda     #R_SKIP
        ldx     #t_f
        tst     coco3
        beq     @nof
        ldx     #F_PHYS
        ldy     #F_PHYS_SUM
        ldu     #t_f
        jsr     ldtest
        bra     @f
@nof    jsr     cell
@f
        ifdef   HWBIG
        ldx     #TAIL           ; past track 34, in the padded part
        ldy     #TAIL_SUM
        ldu     #t_tail
        jsr     ldtest
        endc
        ldx     #B_PACK         ; out of order: restart, skip to the offset
        ldy     #B_PACK_SUM
        ldu     #t_reload
        jsr     ldtest
;
; ---- mounting by name
;
        ldx     #S_SDC_MountName
        ldy     #S_SDC_BuildID
        jsr     sdc_open
        jsr     got
        ldx     #t_mount
        jsr     cell
        tsta
        bne     @nos
        ldx     #S_DATA
        ldy     #S_DATA_SUM
        ldu     #t_sdata
        jsr     ldtest
        ldx     #S_ALIGN
        ldy     #S_ALIGN_SUM
        ldu     #t_salign
        jsr     ldtest
@nos    ldx     #nofile
        ldy     #0
        jsr     sdc_open
        ldb     #SDC_EMOUNT
        jsr     want
        ldx     #t_nofile
        jsr     cell
        ldx     #home           ; put the disk back in drive 0
        ldy     #SDC_BuildID
        jsr     sdc_open
        jsr     got
        ldx     #t_home
        jsr     cell
;
; ---- summary, back to BASIC
;
finish  jsr     sdc_close
        jsr     newrow
        lda     fails
        bne     @fail
        ldx     #t_pass
        jsr     text
        bra     @out
@fail   ldx     #vbuf           ; up to 99 failures
        ldb     #'0'
@tens   cmpa    #10
        blo     @units
        suba    #10
        incb
        bra     @tens
@units  cmpb    #'0'
        beq     @one
        stb     ,x+
@one    adda    #'0'
        sta     ,x+
        clr     ,x
        ldx     #vbuf
        jsr     text
        ldx     #t_failed
        jsr     text
@out    jsr     newrow
        ldd     cursor          ; BASIC prints OK below the results
        std     CURPOS
        andcc   #$AF            ; interrupts back on
        lda     fails
        rts

;------------------------------------------------------------------------------
; ldtest: wipe an asset's memory, load it, check its bytes, print the cell
; Entry: X = descriptor, Y = checksum entry (NAME_SUM), U = label
; Exit : lres = result, lastu = U from sdc_load (exec address)
;------------------------------------------------------------------------------
ldtest  pshs    x,y,u
        jsr     wipe
        jsr     sdc_load
        bcs     @res
        stu     lastu
        jsr     check
@res    sta     lres
        ldx     4,s             ; the label
        jsr     cell
        puls    x,y,u,pc

; got: after a call that should succeed. A = R_OK, or the error code
got     bcc     @ok
        rts
@ok     clra
        rts

; want: after a call that should fail with error B. A = R_OK, R_E0 (no
; error) or the wrong error code
want    bcs     @err
        lda     #R_E0
        rts
@err    pshs    b
        cmpa    ,s+
        bne     @r
        clra
@r      rts

; same: A = R_OK if Z is set (a compare matched), else R_BAD
same    beq     @ok
        lda     #R_BAD
        rts
@ok     clra
        rts

;------------------------------------------------------------------------------
; wipe / check: walk an asset's regions (Y = checksum entry). wipe zeroes
; them, check compares their sums; phys regions go through the MMU window.
; Exit: check: A = R_OK or R_BAD. X, Y, U preserved.
;------------------------------------------------------------------------------
wipe    clr     op
        bra     walk
check   lda     #1
        sta     op
walk    pshs    x,y,u
        tfr     y,u             ; U walks the entry
        clr     bad
        lda     ,u+
        sta     nreg
@reg    lda     ,u+             ; flags
        sta     rflag
        bpl     @hdr
        ldb     SDC_MMUREG      ; phys: save the window's block
        stb     msave
@hdr    lda     ,u+
        sta     ahi
        ldd     ,u++
        std     alo
        ldd     ,u++
        std     left
        ldd     #0
        std     r1
        std     r2
@chunk  tst     rflag
        bmi     @phys
        ldx     alo             ; CPU address: the whole region at once
        ldd     left
        bra     @do
@phys   lda     ahi             ; block = address bits 20-13
        ldb     alo
        lsra
        rorb
        lsra
        rorb
        lsra
        rorb
        lsra
        rorb
        lsra
        rorb
        stb     SDC_MMUREG
        ldd     alo
        anda    #$1F
        addd    #SDC_WINDOW
        tfr     d,x
        ldd     #SDC_WEND       ; as far as the window's end
        pshs    x
        subd    ,s++
        cmpd    left
        bls     @do
        ldd     left
@do     std     n
        tfr     d,y
        tst     op
        bne     @sum
@zero   clr     ,x+
        leay    -1,y
        bne     @zero
        bra     @next
@sum    ldb     ,x+
        clra
        addd    r1
        std     r1
        addd    r2
        std     r2
        leay    -1,y
        bne     @sum
@next   ldd     alo             ; address += n
        addd    n
        std     alo
        bcc     @nc
        inc     ahi
@nc     ldd     left
        subd    n
        std     left
        bne     @chunk
        tst     rflag
        bpl     @cmp
        ldb     msave
        stb     SDC_MMUREG
@cmp    ldd     ,u++            ; expected sums
        tst     op
        beq     @skip2
        cmpd    r1
        bne     @bad
        ldd     ,u
        cmpd    r2
        beq     @skip2
@bad    inc     bad
@skip2  leau    2,u
        dec     nreg
        lbne    @reg
        lda     #R_OK
        tst     bad
        beq     @r
        lda     #R_BAD
@r      puls    x,y,u,pc

;------------------------------------------------------------------------------
; Screen output. Cells are 16 characters: an 11-character label, then the
; result. Characters go to the screen as VDG codes, normal or inverse.
;------------------------------------------------------------------------------

; cell: X = label, A = result
cell    pshs    a,b,x,y
        ldy     cursor
        ldb     #11
@lab    lda     ,x+
        beq     @pad
        anda    #$3F
        ora     #$40
        sta     ,y+
        decb
        bne     @lab
        bra     @res
@pad    lda     #$60
@pad2   sta     ,y+
        decb
        bne     @pad2
@res    lda     ,s
        cmpa    #R_NONE
        beq     @done
        ldx     #t_ok
        tsta
        beq     @norm
        ldx     #t_skip
        cmpa    #R_SKIP
        beq     @norm
        ldx     rtext
        cmpa    #R_TEXT
        beq     @norm
        inc     fails
        ldx     #t_bad
        cmpa    #R_BAD
        beq     @inv
        ldx     #t_e0
        cmpa    #R_E0
        beq     @inv
        ldb     #'E'&$3F        ; En, inverse
        stb     ,y+
        adda    #'0'
        anda    #$3F
        sta     ,y+
        bra     @done
@inv    lda     ,x+
        beq     @done
        anda    #$3F
        sta     ,y+
        bra     @inv
@norm   lda     ,x+
        beq     @done
        anda    #$3F
        ora     #$40
        sta     ,y+
        bra     @norm
@done   ldd     cursor
        addd    #16
        std     cursor
        puls    a,b,x,y,pc

; text: print the string at X at the cursor
text    pshs    a,y
        ldy     cursor
@l      lda     ,x+
        beq     @e
        anda    #$3F
        ora     #$40
        sta     ,y+
        bra     @l
@e      sty     cursor
        puls    a,y,pc

; newrow: move the cursor to the start of the next row (unless at one)
newrow  ldd     cursor
        addd    #31
        andb    #$E0
        std     cursor
        rts

; fmtver: D = BCD version ($0128) -> "1.28" in vbuf
fmtver  ldx     #vbuf
        pshs    b
        cmpa    #$10
        blo     @one
        bsr     hexhi
@one    bsr     hexlo
        ldb     #'.'
        stb     ,x+
        puls    a
        bsr     hexhi
        bsr     hexlo
        clr     ,x
        rts
hexhi   pshs    a
        lsra
        lsra
        lsra
        lsra
        adda    #'0'
        sta     ,x+
        puls    a,pc
hexlo   pshs    a
        anda    #$0F
        adda    #'0'
        sta     ,x+
        puls    a,pc

;------------------------------------------------------------------------------
; text and variables
;------------------------------------------------------------------------------
        ifdef   HWBIG
t_title fcn     "DMPKG HARDWARE TEST  HWBIG"
home    fcn     "M:HWBIG.DSK"
t_home  fcn     "REMOUNT"
        else
t_title fcn     "DMPKG HARDWARE TEST  HWTEST"
home    fcn     "M:HWTEST.DSK"
t_home  fcn     "REMOUNT"
        endc
nofile  fcn     "M:NOSUCH.SDS"
t_sdc   fcn     "SDC FW"
t_coco2 fcn     "COCO 1/2"
t_coco3 fcn     "COCO 3"
t_badid fcn     "BAD ID"
t_open  fcn     "OPEN"
t_stop  fcn     "STOP"
t_a     fcn     "A_ODD"
t_b     fcn     "B_PACK"
t_c     fcn     "C_MULTI"
t_d     fcn     "D_ALIGN"
t_e     fcn     "E_CODE"
t_exec  fcn     "EXEC"
t_f     fcn     "F_PHYS"
t_tail  fcn     "TAIL"
t_reload fcn    "RELOAD B"
t_mount fcn     "MOUNT SDS"
t_sdata fcn     "S_DATA"
t_salign fcn    "S_ALIGN"
t_nofile fcn    "NO FILE"
t_ok    fcn     "OK"
t_skip  fcn     "SKIP"
t_bad   fcn     "BAD"
t_e0    fcn     "E0"
t_pass  fcn     "ALL PASSED"
t_failed fcn    " FAILED"

coco3   fcb     0               ; nonzero on a CoCo 3
cursor  rmb     2
fails   rmb     1
rtext   rmb     2
lres    rmb     1
lastu   rmb     2
badid   rmb     4
vbuf    rmb     6
op      rmb     1               ; walk: 0 wipe, 1 check
bad     rmb     1
nreg    rmb     1
rflag   rmb     1
msave   rmb     1
ahi     rmb     1               ; region address, 24-bit
alo     rmb     2
left    rmb     2
n       rmb     2
r1      rmb     2
r2      rmb     2

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"
        include "stream.inc"
        include "streamtab.asm"
        include "hwsums.inc"

        end     start
