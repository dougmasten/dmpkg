;------------------------------------------------------------------------------
; dmpkg.asm - CoCo SDC STREAM loader for dmpkg images
;
; 6809 source (runs on HD6309 in either mode), lwasm syntax.
; Pair with the files dmpkg generates:  equates (EQUs) and table
; (mount name, build_id, 12-byte asset descriptors).
;
; Entry points
;   sdc_init   Checks a CoCo SDC is present (SDC_ENODEV if not), then gets
;              its firmware version like sdc_version. sdc_open calls it.
;   sdc_version
;              Enters command mode, returns D = firmware version in BCD
;              (e.g. $0124 for 124), or D = 0 if the firmware predates the
;              VERSION command (before 113). Also stored in sdc_ver.
;              Ends a running stream.
;   sdc_open   X = mount name ("M:..." zero-terminated, e.g. SDC_MountName),
;                  or 0 to use the image already in SDC_DRIVE (a dmpkg disk
;                  image the loader was run from)
;              Y = expected 4-byte build_id (SDC_BuildID) or 0 to skip the check
;              Checks a CoCo SDC is present and its firmware supports STREAM
;              (VERSION >= SDC_MINVER), mounts the image, starts a stream at
;              block 0 and checks the build_id so a stale image is caught.
;   sdc_stop   ends a running stream ($D0), stays in command mode
;   sdc_close  sdc_stop, then puts the controller back in floppy
;              emulation mode (write 0 to $FF40)
;   sdc_load   X = asset descriptor
;              Loads the asset to its destination. If the stream is already
;              positioned at the asset (packed assets loaded in image
;              order) it carries on without restarting the stream.
;              decb assets: returns exec address in U.
;   sdc_getbyte
;              Returns the next stream byte in A.
;
; All return carry clear on success, or carry set and A = error code:
;   SDC_EMOUNT  1  mount failed (status bit 7 after $E0)
;   SDC_EHDR    2  build_id in block 0 doesn't match (stale or wrong image)
;   SDC_EEOF    3  stream ended (BUSY dropped) before the data was read
;   SDC_ETIME   4  controller didn't answer in time
;   SDC_EFAIL   5  controller reported an error (FAILED, or the stream ended
;                  with status $08: card error)
;   SDC_EVER    6  firmware too old for STREAM (sdc_ver holds its version)
;   SDC_ENODEV  7  no CoCo SDC found
; On any error the stream is stopped. Registers other than A/CC are
; preserved, except B for sdc_init and sdc_version (they return D) and U
; for decb loads (exec address).
;
; Protocol (CoCo SDC user guide ch. 7, firmware with STREAM support):
;   presence: $64 then $00 -> $FF42 (Flash data), $FF43 reads differ by $60
;   $43 -> $FF40 command mode; status $FF48: b0 BUSY, b1 READY, b7 FAILED
;   'V' -> $FF49, $C0 -> $FF48: version, BCD in $FF4A/$FF4B (FAILED if <113)
;   $E0+drv mount, 256-byte name block written to $FF4A/$FF4B
;   block# -> $FF49/$FF4A/$FF4B, $90+drv -> $FF48, poll READY,
;   then 512 bytes per block read from $FF4A then $FF4B (LDD $FF4A),
;   READY polled again before each following block. $D0 aborts.
;
; Configuration - define any of these before including to override:
;   SDC_DRIVE    0      SDC drive slot used for the image (0 or 1)
;   SDC_WINDOW   $4000  logical 8K window for phys (CoCo 3) loads, 8K-aligned,
;                       $2000-$C000
;   SDC_MMUREG          MMU register that maps SDC_WINDOW; default is the
;                       task 0 one, $FFA0+SDC_WINDOW/$2000 ($FFA2 for $4000).
;                       Define it for task 1 ($FFA8+...); it must be $FFA0-$FFAF
;                       and map the same 8K slot as SDC_WINDOW
;   SDC_MMU_HOME        if defined, block restored into SDC_MMUREG after a
;                       phys load; otherwise the value read back before
;                       the load is restored
;   SDC_DELAY    12     settle loop before polling READY (8 cyc/pass: ~54us at
;                       1.79MHz; the SDC needs ~20us to update READY; 1-65535)
;   SDC_TIMEOUT  16     poll timeout, units of 65536 polls (1-255)
;   SDC_MINVER   $0117  lowest firmware sdc_open accepts (117 added STREAM)
;   SDC_NOVERCHK        if defined, sdc_open skips the version check
;------------------------------------------------------------------------------

        ifndef  SDC_DRIVE
SDC_DRIVE       equ     0
        endc
        if      SDC_DRIVE&$FFFE         ; added to the $E0/$90 command codes
        error   "SDC_DRIVE must be 0 or 1"
        endc
        ifndef  SDC_WINDOW
SDC_WINDOW      equ     $4000
        endc
        if      SDC_WINDOW&$1FFF
        error   "SDC_WINDOW must be 8K-aligned"
        endc
        if      SDC_WINDOW<$2000        ; code tests the window's high byte
        error   "SDC_WINDOW must be $2000-$C000"
        endc
        if      SDC_WINDOW>$C000        ; $E000 block holds I/O and vectors
        error   "SDC_WINDOW must be $2000-$C000"
        endc
SDC_WEND        equ     SDC_WINDOW+$2000        ; end of the window
        ifndef  SDC_MMUREG
SDC_MMUREG      equ     $FFA0+SDC_WINDOW/$2000  ; task 0 register for the window
        endc
        if      SDC_MMUREG<$FFA0
        error   "SDC_MMUREG must be an MMU register, $FFA0-$FFAF"
        endc
        if      SDC_MMUREG>$FFAF
        error   "SDC_MMUREG must be an MMU register, $FFA0-$FFAF"
        endc
        if      (SDC_MMUREG&7)-(SDC_WINDOW/$2000)
        error   "SDC_MMUREG doesn't map SDC_WINDOW"
        endc
        ifndef  SDC_DELAY
SDC_DELAY       equ     12
        endc
        if      SDC_DELAY<1             ; X counts down: 0 would be 65536 passes
        error   "SDC_DELAY must be 1-65535"
        endc
        if      SDC_DELAY>$FFFF
        error   "SDC_DELAY must be 1-65535"
        endc
        ifndef  SDC_TIMEOUT
SDC_TIMEOUT     equ     16
        endc
        if      SDC_TIMEOUT<1           ; A counts down: 0 would be 256
        error   "SDC_TIMEOUT must be 1-255"
        endc
        if      SDC_TIMEOUT>255
        error   "SDC_TIMEOUT must be 1-255"
        endc
        ifndef  SDC_MINVER
SDC_MINVER      equ     $0117
        endc

SDC_LATCH       equ     $FF40           ; control latch (write)
SDC_FDATA       equ     $FF42           ; Flash data register (write)
SDC_FCTL        equ     $FF43           ; Flash control register (bank)
SDC_STAT        equ     $FF48           ; command (write) / status (read)
SDC_P1          equ     $FF49           ; parameter 1 / block high byte
SDC_DATA        equ     $FF4A           ; parameter 2/3, data port ($FF4A,$FF4B)

SDC_CMDMODE     equ     $43
SDC_C_MOUNT     equ     $E0+SDC_DRIVE
SDC_C_STREAM    equ     $90+SDC_DRIVE
SDC_C_ABORT     equ     $D0
SDC_C_QUERY     equ     $C0

SDC_BUSY        equ     %00000001
SDC_READY       equ     %00000010
SDC_ERROR       equ     %00001000       ; set when a stream ends on an error

; error codes, returned in A with carry set
SDC_EMOUNT      equ     1               ; mount failed (file not found?)
SDC_EHDR        equ     2               ; build_id mismatch: stale image
SDC_EEOF        equ     3               ; stream ended before the data
SDC_ETIME       equ     4               ; controller didn't answer in time
SDC_EFAIL       equ     5               ; controller reported an error
SDC_EVER        equ     6               ; firmware older than SDC_MINVER
SDC_ENODEV      equ     7               ; no CoCo SDC found

SDC_F_PHYS      equ     $80
SDC_F_DECB      equ     $40

; asset descriptor (12 bytes, as dmpkg generates them in the table)
sdc_desc        struct
blk             rmb     3               ; block where the data starts
off             rmb     2               ; byte offset in that block (0-511)
len             rmb     3               ; stored length
flags           rmb     1               ; SDC_F_PHYS, SDC_F_DECB
dest            rmb     3               ; CPU or physical address
                endstruct

;------------------------------------------------------------------------------
; state
sdc_blk         rmb     3       ; block holding the next stream byte
sdc_off         rmb     2       ; offset of the next byte in that block
sdc_live        fcb     0       ; nonzero while a stream is running
sdc_poll        rmb     1       ; nonzero: wait READY before the next byte
sdc_left        rmb     3       ; bytes still to copy (24-bit)
sdc_chunk       rmb     2       ; bytes in the current copy chunk
sdc_n           rmb     1       ; 32-byte groups left in the chunk
sdc_phys        fcb     0       ; nonzero only during a phys copy
sdc_mblk        rmb     1       ; block mapped in the window
sdc_msave       rmb     1       ; window block before the load
sdc_tmo         rmb     1       ; timeout outer counter
sdc_ver         fdb     0       ; firmware version (BCD), set by sdc_version

;------------------------------------------------------------------------------
; Function    : sdc_init
; Entry       : none
; Exit        : CC.C clear: D = firmware version in BCD ($0124 = 124),
;             :             0 if the firmware predates VERSION (before 113)
;             : CC.C set  : A = SDC_ENODEV or SDC_ETIME
; Destroys    : A, CC (B too when the SDC is found)
; Calls       : sdc_version
; Description : Checks a CoCo SDC is present, then gets its firmware version
;             : (also kept in sdc_ver). The check writes the Flash data
;             : register ($FF42) and reads the Flash control register
;             : ($FF43), whose bits 5-6 follow it on an SDC; without one, it
;             : returns SDC_ENODEV before any command is written. If found,
;             : the controller is left in command mode (sdc_close leaves it).
;------------------------------------------------------------------------------
sdc_init
        lda     #$64                    ; Flash data: bits 5-6 set
        sta     SDC_FDATA
        lda     SDC_FCTL
        clr     SDC_FDATA               ; Flash data: bits 5-6 clear
        eora    SDC_FCTL                ; bits that followed the data
        cmpa    #$60
        beq     sdc_version             ; found: get the firmware version
        lda     #SDC_ENODEV             ; ERROR CODE: no CoCo SDC
        orcc    #1                      ; set carry (error)
        rts

;------------------------------------------------------------------------------
; Function    : sdc_version
; Entry       : none
; Exit        : CC.C clear: D = firmware version in BCD ($0124 = 124),
;             :             0 if the firmware predates VERSION (before 113)
;             : CC.C set  : A = SDC_ETIME
; Destroys    : A, B, CC
; Calls       : sdc_stop, sdc_waitidle
; Description : Ends any running stream, enters command mode and sends the
;             : VERSION command ($C0, 'V' in $FF49). The result is also kept
;             : in sdc_ver.
;------------------------------------------------------------------------------
sdc_version
        jsr     sdc_stop
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        jsr     sdc_waitidle
        bcs     @exit
        lda     #'V
        sta     SDC_P1
        lda     #SDC_C_QUERY
        sta     SDC_STAT
        jsr     sdc_waitidle
        bcs     @exit
        ldd     #0
        tst     SDC_STAT                ; FAILED: no VERSION command (< 113)
        bmi     @set_ver
        ldd     SDC_DATA
@set_ver
        std     sdc_ver
        andcc   #$fe                    ; clear carry (success)
@exit   rts

;------------------------------------------------------------------------------
; Function    : sdc_open
; Entry       : Reg X = mount name ("M:..." zero-terminated, e.g. SDC_MountName),
;             :         or 0 to stream the image already in SDC_DRIVE
;             :     Y = expected 4-byte build_id (SDC_BuildID), or 0 to skip it
; Exit        : CC.C clear: image mounted, stream parked after the build_id
;             : CC.C set  : A = error code (stream stopped)
; Destroys    : A, CC
; Calls       : sdc_init (sdc_stop with SDC_NOVERCHK), sdc_waitidle,
;             : sdc_waitready, sdc_start, sdc_fail
; Description : Checks a CoCo SDC is present and its firmware supports STREAM
;             : (VERSION >= SDC_MINVER), mounts the image (unless X = 0),
;             : starts a stream at block 0 and compares the header's build_id
;             : (bytes 8-11) so a stale image is caught.
;------------------------------------------------------------------------------
sdc_open
        pshs    b,x,y,u
        ifdef   SDC_NOVERCHK
        jsr     sdc_stop
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        jsr     sdc_waitidle
        bcs     @err
        else
        jsr     sdc_init                ; SDC present? stops any stream,
        bcs     @err                    ; enters command mode
        cmpd    #SDC_MINVER
        bhs     @ver_ok
        lda     #SDC_EVER               ; ERROR CODE: firmware < 117
        bra     @err
@ver_ok
        endc
        leax    ,x                      ; X = 0: no mount, the image is
        beq     @mount_ok               ; already in SDC_DRIVE
        lda     #SDC_C_MOUNT
        sta     SDC_STAT
        jsr     sdc_waitready
        bcs     @err
        clra                            ; A counts 256 bytes, 2 per pass
@name_loop
        ldb     ,x                      ; name byte, or 0 padding once the
        beq     @name_1                 ; terminator is reached (X parks on it)
        leax    1,x
@name_1 stb     SDC_DATA
        ldb     ,x
        beq     @name_2
        leax    1,x
@name_2 stb     SDC_DATA+1
        adda    #2
        bne     @name_loop
        jsr     sdc_waitidle
        bcs     @err
        lda     SDC_STAT
        bpl     @mount_ok
        lda     #SDC_EMOUNT             ; ERROR CODE: file not found
        bra     @err
@mount_ok
        clra                            ; start stream at block 0
        ldu     #0
        jsr     sdc_start
        bcs     @err
        ldy     3,s                     ; expected build_id pointer (saved Y)
        beq     @ok
        ldd     SDC_DATA                ; drop header bytes 0-7
        ldd     SDC_DATA                ;  "     "      "    "
        ldd     SDC_DATA                ;  "     "      "    "
        ldd     SDC_DATA                ;  "     "      "    "
        ldd     SDC_DATA                ; bytes 8-11: build_id
        cmpd    ,y
        bne     @ehdr                   ; fail build_id check
        ldd     SDC_DATA
        cmpd    2,y
        bne     @ehdr                   ; fail build_id check
        ldd     #12                     ; stream position after the build_id
        std     sdc_off
@ok     andcc   #$fe                    ; clear carry (success)
        puls    b,x,y,u,pc
@ehdr   lda     #SDC_EHDR               ; ERROR CODE: stale image
@err    jsr     sdc_fail
        puls    b,x,y,u,pc

;------------------------------------------------------------------------------
; Function    : sdc_stop
; Entry       : none
; Exit        : CC.C clear
; Destroys    : CC
; Calls       : sdc_waitidle
; Description : Aborts a running stream ($D0) and waits for the controller.
;             : Stays in command mode. Does nothing if no stream is running.
;------------------------------------------------------------------------------
sdc_stop
        pshs    a
        tst     sdc_live
        beq     @exit
        clr     sdc_live
        lda     #SDC_C_ABORT
        sta     SDC_STAT
        jsr     sdc_waitidle
@exit   andcc   #$fe                    ; clear carry (success)
        puls    a,pc

;------------------------------------------------------------------------------
; Function    : sdc_close
; Entry       : none
; Exit        : CC.C clear
; Destroys    : CC
; Calls       : sdc_stop
; Description : Stops any stream, then returns the controller to floppy
;             : emulation mode (0 to $FF40).
;------------------------------------------------------------------------------
sdc_close
        bsr     sdc_stop
        clr     SDC_LATCH
        rts

;------------------------------------------------------------------------------
; Function    : sdc_fail
; Entry       : Reg A = error code
; Exit        : CC.C set, A = error code
; Destroys    : CC
; Calls       : sdc_stop
; Description : Common error exit: stops the stream and sets carry.
;------------------------------------------------------------------------------
sdc_fail
        bsr     sdc_stop                ; preserves A
        orcc    #1                      ; set carry (error)
        rts

;------------------------------------------------------------------------------
; Function    : sdc_load
; Entry       : Reg X = 12-byte asset descriptor
; Exit        : CC.C clear: asset loaded; decb assets: U = exec address
;             : CC.C set  : A = error code (stream stopped)
; Destroys    : A, CC (U for decb assets)
; Calls       : sdc_start, sdc_copy, sdc_getword, sdc_fail
; Description : Loads the asset to its destination. If the stream is already
;             : parked at the asset (packed assets loaded in image order) it
;             : carries on; otherwise it restarts at the asset's block and drops
;             : the bytes before its offset. Phys assets go through the MMU
;             : window; decb assets follow their records.
;------------------------------------------------------------------------------
sdc_load
        pshs    b,x,y
        tst     sdc_live                ; is the stream already parked here?
        beq     @seek
        ldd     sdc_desc.blk,x
        cmpd    sdc_blk
        bne     @seek
        lda     sdc_desc.blk+2,x
        cmpa    sdc_blk+2
        bne     @seek
        ldd     sdc_desc.off,x
        cmpd    sdc_off
        beq     @here
@seek   pshs    u
        lda     sdc_desc.blk,x
        ldu     sdc_desc.blk+1,x
        jsr     sdc_start
        puls    u
        lbcs    @err
        ldd     sdc_desc.off,x          ; skip to the asset's offset (0-511)
        beq     @here
        std     sdc_off                 ; stream position once skipped
        lsra                            ; D = whole pairs to drop
        rorb
        tfr     d,y
        leay    ,y                      ; (TFR leaves the flags alone)
        beq     @skip_odd
@skip_loop
        ldd     SDC_DATA                ; drop a pair: $FF4A then $FF4B
        leay    -1,y
        bne     @skip_loop
@skip_odd
        lda     sdc_desc.off+1,x
        bita    #1
        beq     @here
        lda     SDC_DATA                ; odd: drop the pair's 1st byte, so
@here                                   ; the next read is $FF4B
        lda     sdc_desc.flags,x
        bita    #SDC_F_DECB
        bne     @decb
        ldd     sdc_desc.len,x
        std     sdc_left
        lda     sdc_desc.len+2,x
        sta     sdc_left+2
        tst     sdc_desc.flags,x        ; SDC_F_PHYS is bit 7
        bmi     @phys
        pshs    u
        ldu     sdc_desc.dest+1,x
        jsr     sdc_copy
        puls    u
        bcs     @err
        bra     @ok
; CoCo 3 physical load through the MMU window
@phys   pshs    u
        ifdef   SDC_MMU_HOME
        lda     #SDC_MMU_HOME
        else
        lda     SDC_MMUREG
        endc
        sta     sdc_msave
        ldd     sdc_desc.dest,x         ; block = dest >> 13: A:B = bits 23-8,
        aslb                            ; shifted left 3 leaves bits 20-13 in A
        rola
        aslb
        rola
        aslb
        rola
        sta     sdc_mblk
        sta     SDC_MMUREG
        ldd     sdc_desc.dest+1,x       ; window offset = dest & $1FFF
        anda    #$1F
        addd    #SDC_WINDOW
        tfr     d,u
        sta     sdc_phys                ; nonzero (window is never in page 0)
        jsr     sdc_copy
        pshs    a,cc
        lda     sdc_msave
        sta     SDC_MMUREG
        clr     sdc_phys
        puls    a,cc
        puls    u
        bcs     @err
        bra     @ok
; decb records: [len.w addr.w data]... [0000 exec.w]
@decb   clr     sdc_left
@decb_loop
        jsr     sdc_getword             ; D = length
        bcs     @err
        std     sdc_left+1
        beq     @decb_end
        jsr     sdc_getword             ; D = load address
        bcs     @err
        tfr     d,u
        jsr     sdc_copy
        bcs     @err
        bra     @decb_loop
@decb_end
        jsr     sdc_getword             ; exec address
        bcs     @err
        tfr     d,u
@ok     andcc   #$fe                    ; clear carry (success)
        puls    b,x,y,pc
@err    jsr     sdc_fail
        puls    b,x,y,pc

;------------------------------------------------------------------------------
; Function    : sdc_start
; Entry       : Reg A = block number bits 23-16
;             :     U = block number bits 15-0
; Exit        : CC.C clear: stream running, first block READY
;             : CC.C set  : A = error code
; Destroys    : A, CC
; Calls       : sdc_stop, sdc_waitidle, sdc_waitready
; Description : Stops any running stream, then starts a new one at the block
;             : and resets the stream position to offset 0.
;------------------------------------------------------------------------------
sdc_start
        jsr     sdc_stop
        sta     sdc_blk
        stu     sdc_blk+1
        sta     SDC_P1
        stu     SDC_DATA
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        bsr     sdc_waitidle
        bcs     @exit
        lda     #SDC_C_STREAM
        sta     SDC_STAT
        bsr     sdc_waitready
        bcs     @exit
        clr     sdc_off
        clr     sdc_off+1
        clr     sdc_poll
        inc     sdc_live
@exit   rts

;------------------------------------------------------------------------------
; Function    : sdc_settle
; Entry       : none
; Exit        : none
; Destroys    : CC
; Calls       : none
; Description : Delay loop of SDC_DELAY passes, so the SDC has ~20us after a
;             : command before its registers are read.
;------------------------------------------------------------------------------
sdc_settle
        pshs    x
        ldx     #SDC_DELAY
@loop   leax    -1,x
        bne     @loop
        puls    x,pc

;------------------------------------------------------------------------------
; Function    : sdc_waitidle
; Entry       : none
; Exit        : CC.C clear: BUSY clear
;             : CC.C set  : A = SDC_ETIME
; Destroys    : A, CC
; Calls       : sdc_settle
; Description : Settles, then polls status until BUSY clears or the
;             : SDC_TIMEOUT x 65536 poll limit runs out.
;------------------------------------------------------------------------------
sdc_waitidle
        bsr     sdc_settle
        pshs    x
        lda     #SDC_TIMEOUT
        sta     sdc_tmo
        ldx     #0
@loop   lda     SDC_STAT
        bita    #SDC_BUSY
        beq     @ok
        leax    -1,x
        bne     @loop
        dec     sdc_tmo
        bne     @loop
        lda     #SDC_ETIME              ; ERROR CODE: BUSY timeout
        orcc    #1                      ; set carry (error)
        puls    x,pc
@ok     andcc   #$fe                    ; clear carry (success)
        puls    x,pc

;------------------------------------------------------------------------------
; Function    : sdc_waitready
; Entry       : none
; Exit        : CC.C clear: READY set
;             : CC.C set  : A = SDC_EFAIL (FAILED, or BUSY dropped with
;             :             SDC_ERROR), SDC_EEOF (BUSY dropped, status $00:
;             :             end of file) or SDC_ETIME
; Destroys    : A, CC
; Calls       : sdc_settle
; Description : Settles, then polls status until READY is set, the controller
;             : fails, the stream ends, or the poll limit runs out.
;------------------------------------------------------------------------------
sdc_waitready
        bsr     sdc_settle
        pshs    x
        lda     #SDC_TIMEOUT
        sta     sdc_tmo
        ldx     #0
@loop   lda     SDC_STAT
        bmi     @fail
        bita    #SDC_READY
        bne     @ok
        bita    #SDC_BUSY
        beq     @eof
        leax    -1,x
        bne     @loop
        dec     sdc_tmo
        bne     @loop
        lda     #SDC_ETIME              ; ERROR CODE: READY timeout
        fcb     $8C                     ; skip 2 bytes
@fail   lda     #SDC_EFAIL              ; ERROR CODE: SDC reported FAILED
@err    orcc    #1                      ; set carry (error)
        puls    x,pc
@eof    bita    #SDC_ERROR              ; firmware ends a failed stream with
        bne     @fail                   ; $08 (no FAILED bit), a clean one $00
        lda     #SDC_EEOF               ; ERROR CODE: end of file
        bra     @err
@ok     andcc   #$fe                    ; clear carry (success)
        puls    x,pc

;------------------------------------------------------------------------------
; Function    : sdc_getbyte
; Entry       : none
; Exit        : CC.C clear: A = next stream byte
;             : CC.C set  : A = error code
; Destroys    : A, CC
; Calls       : sdc_waitready, sdc_nextblk
; Description : Reads one byte from $FF4A (even offset) or $FF4B (odd offset),
;             : waiting for READY first at the start of each block.
;------------------------------------------------------------------------------
sdc_getbyte
        pshs    b,x
        tst     sdc_poll
        beq     @ready
        bsr     sdc_waitready
        bcs     @exit
        clr     sdc_poll
@ready  ldx     sdc_off
        tfr     x,d
        bitb    #1
        bne     @odd
        lda     SDC_DATA                ; even offset: first byte of the pair
        bra     @adv
@odd    lda     SDC_DATA+1              ; odd offset: second byte
@adv    leax    1,x
        cmpx    #512
        bne     @set_off
        bsr     sdc_nextblk
        ldx     #0
@set_off
        stx     sdc_off
        andcc   #$fe                    ; clear carry (success)
@exit   puls    b,x,pc

;------------------------------------------------------------------------------
; Function    : sdc_getword
; Entry       : none
; Exit        : CC.C clear: D = next two stream bytes (big-endian)
;             : CC.C set  : A = error code
; Destroys    : A, B, CC
; Calls       : sdc_getbyte
; Description : Reads a 16-bit big-endian value from the stream.
;------------------------------------------------------------------------------
sdc_getword
        bsr     sdc_getbyte
        bcs     @error
        tfr     a,b                     ; B = 1st byte (sdc_getbyte keeps B)
        bsr     sdc_getbyte
        bcs     @error                  ; A = error code
        exg     a,b                     ; D = 1st:2nd
@error  rts

;------------------------------------------------------------------------------
; Function    : sdc_nextblk
; Entry       : none
; Exit        : none
; Destroys    : CC
; Calls       : none
; Description : Steps sdc_blk to the next block and flags that READY must be
;             : polled before its first byte is read.
;------------------------------------------------------------------------------
sdc_nextblk
        pshs    a
        inc     sdc_blk+2
        bne     @exit
        inc     sdc_blk+1
        bne     @exit
        inc     sdc_blk
@exit   lda     #1
        sta     sdc_poll
        puls    a,pc

;------------------------------------------------------------------------------
; Function    : sdc_copy
; Entry       : Reg U = destination
;             : sdc_left  = bytes to copy (24-bit)
;             : sdc_phys  = nonzero if U is in the MMU window (sdc_mblk mapped)
; Exit        : CC.C clear: U advanced past the data
;             : CC.C set  : A = error code
; Destroys    : A, CC (U advanced)
; Calls       : sdc_waitready, sdc_nextblk
; Description : Copies stream bytes in chunks capped by the end of the block,
;             : the bytes left and, for phys loads, the end of the window (the
;             : next 8K is mapped when it fills). Inner loop: 32 bytes per
;             : pass, 16x LDD ,X / STD n,U.
;------------------------------------------------------------------------------
sdc_copy
        pshs    b,x,y
        ldx     #SDC_DATA
@loop   lda     sdc_left
        ora     sdc_left+1
        ora     sdc_left+2
        bne     @more
        andcc   #$fe                    ; clear carry (success)
@exit   puls    b,x,y,pc
@more   tst     sdc_poll                ; new block: wait for the buffer
        beq     @nopoll
        jsr     sdc_waitready
        bcs     @exit
        clr     sdc_poll
@nopoll tst     sdc_phys
        beq     @room
        cmpu    #SDC_WEND               ; window full: map the next 8K
        bne     @room
        inc     sdc_mblk
        lda     sdc_mblk
        sta     SDC_MMUREG
        ldu     #SDC_WINDOW
@room   ldd     #512                    ; chunk = bytes left in this block
        subd    sdc_off
        tst     sdc_left                ; capped by bytes left to copy
        bne     @cap_win
        cmpd    sdc_left+1
        bls     @cap_win
        ldd     sdc_left+1
@cap_win
        tst     sdc_phys                ; and by room left in the window
        beq     @set_chunk
        std     sdc_chunk
        ldd     #SDC_WEND
        pshs    u
        subd    ,s++
        cmpd    sdc_chunk
        bls     @set_chunk
        ldd     sdc_chunk
@set_chunk
        std     sdc_chunk
        tfr     d,y                     ; Y = bytes to copy now
        ldd     sdc_left+1              ; sdc_left -= chunk
        subd    sdc_chunk
        std     sdc_left+1
        bcc     @chk_odd
        dec     sdc_left
@chk_odd
        ldb     sdc_off+1               ; odd offset: pick up the 2nd byte first
        bitb    #1
        beq     @even
        lda     1,x
        sta     ,u+
        leay    -1,y
@even   tfr     y,d                     ; split the chunk into 32-byte groups
        andb    #31                     ; and a 0-31 byte tail
        clra
        exg     d,y                     ; Y = tail, D = chunk (0-512)
        aslb                            ; A = D/32 (D < 1024, so D<<3 >> 8)
        rola
        aslb
        rola
        aslb
        rola
        sta     sdc_n
        beq     @w2
        leau    16,u                    ; mid-group: offsets -16..14 fit 5 bits
@w32    ldd     ,x
        std     -16,u
        ldd     ,x
        std     -14,u
        ldd     ,x
        std     -12,u
        ldd     ,x
        std     -10,u
        ldd     ,x
        std     -8,u
        ldd     ,x
        std     -6,u
        ldd     ,x
        std     -4,u
        ldd     ,x
        std     -2,u
        ldd     ,x
        std     ,u
        ldd     ,x
        std     2,u
        ldd     ,x
        std     4,u
        ldd     ,x
        std     6,u
        ldd     ,x
        std     8,u
        ldd     ,x
        std     10,u
        ldd     ,x
        std     12,u
        ldd     ,x
        std     14,u
        leau    32,u
        dec     sdc_n
        bne     @w32
        leau    -16,u                   ; U back to the next free byte
@w2     cmpy    #2
        blo     @w1
        ldd     ,x
        std     ,u++
        leay    -2,y
        bra     @w2
@w1     leay    ,y
        beq     @adv
        lda     ,x                      ; odd tail: 1st byte of the next pair
        sta     ,u+
@adv    ldd     sdc_off                 ; advance stream position
        addd    sdc_chunk
        cmpd    #512
        bne     @set_off
        jsr     sdc_nextblk
        ldd     #0
@set_off
        std     sdc_off
        jmp     @loop
