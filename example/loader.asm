;-------------------------------------------------------------------
; loader.asm - example loader for the single-file disk image
;
; Lives on DEMO.DSK as LOADER.BIN; AUTOEXEC.BAS runs it at power-on, or
; type LOADM"LOADER":EXEC. It streams every asset from the same disk
; image, which is already mounted in drive 0, then returns to BASIC.
; A real program would jump into its code instead (jmp ,u after CODE).
;
; Exit: A = 0, or a dmpkg error code (SDC_E*)
;
; Assemble after a first dmpkg build has written the include files:
;   lwasm --6809 --format=decb -I asm -I example/build -o LOADER.BIN loader.asm
;-------------------------------------------------------------------

        org     $1000           ; above Disk BASIC's work area, below the
                                ; BASIC program at $2601 (AUTOEXEC.BAS)
start
        orcc    #$50            ; mask IRQ/FIRQ: Disk BASIC's motor-off
                                ; timer writes $FF40, which would drop the
                                ; SDC out of command mode mid-stream
        ldx     #DMPKG_MountName ; 0: use the disk already in SDC_DRIVE
        ldy     #DMPKG_BuildID
        jsr     sdc_open
        bcs     @done
        ldx     #FONT           ; in image order, so most loads carry on
        jsr     sdc_load        ; from the running stream
        bcs     @done
        ldx     #PALETTE
        jsr     sdc_load
        bcs     @done
        ldx     #LEVEL1
        jsr     sdc_load
        bcs     @done
        ldx     #MAP
        jsr     sdc_load
        bcs     @done
        ldx     #CODE
        jsr     sdc_load        ; U = exec address
        bcs     @done
        ldx     #TITLE
        jsr     sdc_load
        bcs     @done
        clra                    ; no error
@done
        jsr     sdc_close       ; back to floppy emulation for Disk BASIC
        andcc   #$af            ; interrupts back on
        rts

        include "dmpkg.asm"
        include "assets.inc"
        include "assettab.asm"

        end     start
