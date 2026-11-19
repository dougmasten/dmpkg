;------------------------------------------------------------------------------
; dmpkg.asm - CoCo SDC STREAM loader for dmpkg images
;
; 6809 source (runs on HD6309 in either mode), lwasm syntax.
; Pair with the files dmpkg generates:  equates (EQUs) and table
; (mount name, build_id, 12-byte asset descriptors).
;
; Entry points
;   dmpkg_init   Checks a CoCo SDC is present (DMPKG_ENODEV if not), then gets
;                its firmware version like dmpkg_version. dmpkg_open calls it.
;   dmpkg_version
;                Enters command mode, returns D = firmware version in BCD
;                (e.g. $0124 for 124), or D = 0 if the firmware predates the
;                VERSION command (before 113). Also stored in dmpkg_ver.
;                Ends a running stream.
;   dmpkg_open   X = mount name ("M:..." zero-terminated, e.g. DMPKG_MountName),
;                    or 0 to use the image already in DMPKG_DRIVE (a dmpkg disk
;                    image the loader was run from)
;                Y = expected 4-byte build_id (DMPKG_BuildID) or 0 to skip
;                    the check
;                Checks a CoCo SDC is present and its firmware supports STREAM
;                (VERSION >= DMPKG_MINVER), mounts the image, starts a stream at
;                block 0 and checks the build_id so a stale image is caught.
;   dmpkg_stop   ends a running stream ($D0), stays in command mode
;   dmpkg_close  dmpkg_stop, then puts the controller back in floppy
;                emulation mode (write 0 to $FF40)
;   dmpkg_load   X = asset descriptor
;                Loads the asset to its destination. If the stream is already
;                positioned at the asset (packed assets loaded in image
;                order) it carries on without restarting the stream.
;                decb assets: returns exec address in U.
;   dmpkg_getbyte
;                Returns the next stream byte in A.
;
; All return carry clear on success, or carry set and A = error code:
;   DMPKG_ENODEV  1  no CoCo SDC found
;   DMPKG_EVER    2  firmware too old for STREAM (dmpkg_ver holds its version)
;   DMPKG_EMOUNT  3  mount failed (status bit 7 after $E0)
;   DMPKG_EHDR    4  build_id in block 0 doesn't match (stale or wrong image)
;   DMPKG_EEOF    5  stream ended (BUSY dropped) before the data was read
;   DMPKG_ETIME   6  controller didn't answer in time
;   DMPKG_EFAIL   7  controller reported an error (FAILED, or the stream ended
;                    with status $08: card error)
; On any error the stream is stopped. Registers other than A/CC are
; preserved, except B for dmpkg_init and dmpkg_version (they return D) and U
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
;   DMPKG_DRIVE    0      SDC drive slot used for the image (0 or 1)
;   DMPKG_WINDOW   $4000  logical 8K window for phys (CoCo 3) loads, 8K-aligned,
;                         $2000-$C000
;   DMPKG_MMUREG          MMU register that maps DMPKG_WINDOW; default is the
;                         task 0 one, $FFA0+DMPKG_WINDOW/$2000 ($FFA2 for $4000).
;                         Define it for task 1 ($FFA8+...); it must be $FFA0-$FFAF
;                         and map the same 8K slot as DMPKG_WINDOW
;   DMPKG_MMU_HOME        if defined, block restored into DMPKG_MMUREG after a
;                         phys load; otherwise the value read back before
;                         the load is restored
;   DMPKG_DELAY    12     settle loop before polling READY (8 cyc/pass: ~54us at
;                         1.79MHz; the SDC needs ~20us to update READY; 1-65535)
;   DMPKG_TIMEOUT  16     poll timeout, units of 65536 polls (1-255)
;   DMPKG_MINVER   $0117  lowest firmware dmpkg_open accepts (117 added STREAM)
;   DMPKG_NOVERCHK        if defined, dmpkg_open skips the version check
;------------------------------------------------------------------------------

        ifndef  DMPKG_DRIVE
DMPKG_DRIVE     equ     0
        endc
        if      DMPKG_DRIVE&$FFFE       ; added to the $E0/$90 command codes
        error   "DMPKG_DRIVE must be 0 or 1"
        endc
        ifndef  DMPKG_WINDOW
DMPKG_WINDOW    equ     $4000
        endc
        if      DMPKG_WINDOW&$1FFF
        error   "DMPKG_WINDOW must be 8K-aligned"
        endc
        if      DMPKG_WINDOW<$2000      ; code tests the window's high byte
        error   "DMPKG_WINDOW must be $2000-$C000"
        endc
        if      DMPKG_WINDOW>$C000      ; $E000 block holds I/O and vectors
        error   "DMPKG_WINDOW must be $2000-$C000"
        endc
DMPKG_WEND      equ     DMPKG_WINDOW+$2000      ; end of the window
        ifndef  DMPKG_MMUREG
DMPKG_MMUREG    equ     $FFA0+DMPKG_WINDOW/$2000  ; task 0 register for the window
        endc
        if      DMPKG_MMUREG<$FFA0
        error   "DMPKG_MMUREG must be an MMU register, $FFA0-$FFAF"
        endc
        if      DMPKG_MMUREG>$FFAF
        error   "DMPKG_MMUREG must be an MMU register, $FFA0-$FFAF"
        endc
        if      (DMPKG_MMUREG&7)-(DMPKG_WINDOW/$2000)
        error   "DMPKG_MMUREG doesn't map DMPKG_WINDOW"
        endc
        ifndef  DMPKG_DELAY
DMPKG_DELAY     equ     12
        endc
        if      DMPKG_DELAY<1           ; X counts down: 0 would be 65536 passes
        error   "DMPKG_DELAY must be 1-65535"
        endc
        if      DMPKG_DELAY>$FFFF
        error   "DMPKG_DELAY must be 1-65535"
        endc
        ifndef  DMPKG_TIMEOUT
DMPKG_TIMEOUT   equ     16
        endc
        if      DMPKG_TIMEOUT<1         ; A counts down: 0 would be 256
        error   "DMPKG_TIMEOUT must be 1-255"
        endc
        if      DMPKG_TIMEOUT>255
        error   "DMPKG_TIMEOUT must be 1-255"
        endc
        ifndef  DMPKG_MINVER
DMPKG_MINVER    equ     $0117
        endc

; CoCo SDC registers
SDC_LATCH       equ     $FF40           ; control latch (write)
SDC_FDATA       equ     $FF42           ; Flash data register (write)
SDC_FCTL        equ     $FF43           ; Flash control register (bank)
SDC_STAT        equ     $FF48           ; command (write) / status (read)
SDC_P1          equ     $FF49           ; parameter 1 / block high byte
SDC_DATA        equ     $FF4A           ; parameter 2/3, data port ($FF4A,$FF4B)

; CoCo SDC commands: SDC_CMDMODE goes to SDC_LATCH, the others to SDC_STAT
SDC_CMDMODE     equ     $43
SDC_C_MOUNT     equ     $E0+DMPKG_DRIVE
SDC_C_STREAM    equ     $90+DMPKG_DRIVE
SDC_C_ABORT     equ     $D0
SDC_C_QUERY     equ     $C0

; CoCo SDC status bits, read from SDC_STAT
SDC_BUSY        equ     %00000001
SDC_READY       equ     %00000010
SDC_ERROR       equ     %00001000       ; set when a stream ends on an error

; error codes, returned in A with carry set
DMPKG_ENODEV    equ     1               ; no CoCo SDC found
DMPKG_EVER      equ     2               ; firmware older than DMPKG_MINVER
DMPKG_EMOUNT    equ     3               ; mount failed (file not found?)
DMPKG_EHDR      equ     4               ; build_id mismatch: stale image
DMPKG_EEOF      equ     5               ; stream ended before the data
DMPKG_ETIME     equ     6               ; controller didn't answer in time
DMPKG_EFAIL     equ     7               ; controller reported an error

; asset descriptor flags (dmpkg_desc.flags)
DMPKG_F_PHYS    equ     $80             ; dest is a CoCo 3 physical address
DMPKG_F_DECB    equ     $40             ; data is DECB records (exec in U)

; asset descriptor (12 bytes, as dmpkg generates them in the table)
dmpkg_desc      struct
blk             rmb     3               ; block where the data starts
off             rmb     2               ; byte offset in that block (0-511)
len             rmb     3               ; stored length
flags           rmb     1               ; DMPKG_F_PHYS, DMPKG_F_DECB
dest            rmb     3               ; CPU or physical address
                endstruct

;------------------------------------------------------------------------------
; state
dmpkg_blk       rmb     3       ; block holding the next stream byte
dmpkg_off       rmb     2       ; offset of the next byte in that block
dmpkg_live      fcb     0       ; nonzero while a stream is running
dmpkg_poll      rmb     1       ; nonzero: wait READY before the next byte
dmpkg_left      rmb     3       ; bytes still to copy (24-bit)
dmpkg_chunk     rmb     2       ; bytes in the current copy chunk
dmpkg_n         rmb     1       ; 32-byte groups left in the chunk
dmpkg_phys      fcb     0       ; nonzero only during a phys copy
dmpkg_mblk      rmb     1       ; block mapped in the window
dmpkg_msave     rmb     1       ; window block before the load
dmpkg_tmo       rmb     1       ; timeout outer counter
dmpkg_ver       fdb     0       ; firmware version (BCD), set by dmpkg_version

;------------------------------------------------------------------------------
; Function    : dmpkg_init
; Entry       : none
; Exit        : CC.C clear: D = firmware version in BCD ($0124 = 124),
;             :             0 if the firmware predates VERSION (before 113)
;             : CC.C set  : A = DMPKG_ENODEV or DMPKG_ETIME
; Destroys    : A, CC (B too when the SDC is found)
; Calls       : dmpkg_version
; Description : Checks a CoCo SDC is present, then gets its firmware version
;             : (also kept in dmpkg_ver). The check writes the Flash data
;             : register ($FF42) and reads the Flash control register
;             : ($FF43), whose bits 5-6 follow it on an SDC; without one, it
;             : returns DMPKG_ENODEV before any command is written. If found,
;             : the controller is left in command mode (dmpkg_close leaves it).
;------------------------------------------------------------------------------
dmpkg_init
        lda     #$64                    ; Flash data: bits 5-6 set
        sta     SDC_FDATA
        lda     SDC_FCTL
        clr     SDC_FDATA               ; Flash data: bits 5-6 clear
        eora    SDC_FCTL                ; bits that followed the data
        cmpa    #$60
        beq     dmpkg_version           ; found: get the firmware version
        lda     #DMPKG_ENODEV           ; ERROR CODE: no CoCo SDC
        orcc    #1                      ; set carry (error)
        rts

;------------------------------------------------------------------------------
; Function    : dmpkg_version
; Entry       : none
; Exit        : CC.C clear: D = firmware version in BCD ($0124 = 124),
;             :             0 if the firmware predates VERSION (before 113)
;             : CC.C set  : A = DMPKG_ETIME
; Destroys    : A, B, CC
; Calls       : dmpkg_stop, dmpkg_waitidle
; Description : Ends any running stream, enters command mode and sends the
;             : VERSION command ($C0, 'V' in $FF49). The result is also kept
;             : in dmpkg_ver.
;------------------------------------------------------------------------------
dmpkg_version
        jsr     dmpkg_stop
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        jsr     dmpkg_waitidle
        bcs     @exit
        lda     #'V
        sta     SDC_P1
        lda     #SDC_C_QUERY
        sta     SDC_STAT
        jsr     dmpkg_waitidle
        bcs     @exit
        ldd     #0
        tst     SDC_STAT                ; FAILED: no VERSION command (< 113)
        bmi     @set_ver
        ldd     SDC_DATA
@set_ver
        std     dmpkg_ver
        andcc   #$fe                    ; clear carry (success)
@exit   rts

;------------------------------------------------------------------------------
; Function    : dmpkg_open
; Entry       : Reg X = mount name ("M:..." zero-terminated, e.g. DMPKG_MountName),
;             :         or 0 to stream the image already in DMPKG_DRIVE
;             :     Y = expected 4-byte build_id (DMPKG_BuildID), or 0 to skip it
; Exit        : CC.C clear: image mounted, stream parked after the build_id
;             : CC.C set  : A = error code (stream stopped)
; Destroys    : A, CC
; Calls       : dmpkg_init (dmpkg_stop with DMPKG_NOVERCHK), dmpkg_waitidle,
;             : dmpkg_waitready, dmpkg_start, dmpkg_fail
; Description : Checks a CoCo SDC is present and its firmware supports STREAM
;             : (VERSION >= DMPKG_MINVER), mounts the image (unless X = 0),
;             : starts a stream at block 0 and compares the header's build_id
;             : (bytes 4-7) so a stale image is caught.
;------------------------------------------------------------------------------
dmpkg_open
        pshs    b,x,y,u
        ifdef   DMPKG_NOVERCHK
        jsr     dmpkg_stop
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        jsr     dmpkg_waitidle
        bcs     @err
        else
        jsr     dmpkg_init              ; SDC present? stops any stream,
        bcs     @err                    ; enters command mode
        cmpd    #DMPKG_MINVER
        bhs     @ver_ok
        lda     #DMPKG_EVER             ; ERROR CODE: firmware < 117
        bra     @err
@ver_ok
        endc
        leax    ,x                      ; X = 0: no mount, the image is
        beq     @mount_ok               ; already in DMPKG_DRIVE
        lda     #SDC_C_MOUNT
        sta     SDC_STAT
        jsr     dmpkg_waitready
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
        jsr     dmpkg_waitidle
        bcs     @err
        lda     SDC_STAT
        bpl     @mount_ok
        lda     #DMPKG_EMOUNT           ; ERROR CODE: file not found
        bra     @err
@mount_ok
        clra                            ; start stream at block 0
        ldu     #0
        jsr     dmpkg_start
        bcs     @err
        ldy     3,s                     ; expected build_id pointer (saved Y)
        beq     @ok
        ldd     SDC_DATA                ; drop header bytes 0-3 (magic)
        ldd     SDC_DATA                ;  "     "      "    "
        ldd     SDC_DATA                ; bytes 4-7: build_id
        cmpd    ,y
        bne     @ehdr                   ; fail build_id check
        ldd     SDC_DATA
        cmpd    2,y
        bne     @ehdr                   ; fail build_id check
        ldd     #8                      ; stream position after the build_id
        std     dmpkg_off
@ok     andcc   #$fe                    ; clear carry (success)
        puls    b,x,y,u,pc
@ehdr   lda     #DMPKG_EHDR             ; ERROR CODE: stale image
@err    jsr     dmpkg_fail
        puls    b,x,y,u,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_stop
; Entry       : none
; Exit        : CC.C clear
; Destroys    : CC
; Calls       : dmpkg_waitidle
; Description : Aborts a running stream ($D0) and waits for the controller.
;             : Stays in command mode. Does nothing if no stream is running.
;------------------------------------------------------------------------------
dmpkg_stop
        pshs    a
        tst     dmpkg_live
        beq     @exit
        clr     dmpkg_live
        lda     #SDC_C_ABORT
        sta     SDC_STAT
        jsr     dmpkg_waitidle
@exit   andcc   #$fe                    ; clear carry (success)
        puls    a,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_close
; Entry       : none
; Exit        : CC.C clear
; Destroys    : CC
; Calls       : dmpkg_stop
; Description : Stops any stream, then returns the controller to floppy
;             : emulation mode (0 to $FF40).
;------------------------------------------------------------------------------
dmpkg_close
        bsr     dmpkg_stop
        clr     SDC_LATCH
        rts

;------------------------------------------------------------------------------
; Function    : dmpkg_fail
; Entry       : Reg A = error code
; Exit        : CC.C set, A = error code
; Destroys    : CC
; Calls       : dmpkg_stop
; Description : Common error exit: stops the stream and sets carry.
;------------------------------------------------------------------------------
dmpkg_fail
        bsr     dmpkg_stop              ; preserves A
        orcc    #1                      ; set carry (error)
        rts

;------------------------------------------------------------------------------
; Function    : dmpkg_load
; Entry       : Reg X = 12-byte asset descriptor
; Exit        : CC.C clear: asset loaded; decb assets: U = exec address
;             : CC.C set  : A = error code (stream stopped)
; Destroys    : A, CC (U for decb assets)
; Calls       : dmpkg_start, dmpkg_copy, dmpkg_getword, dmpkg_fail
; Description : Loads the asset to its destination. If the stream is already
;             : parked at the asset (packed assets loaded in image order) it
;             : carries on; otherwise it restarts at the asset's block and drops
;             : the bytes before its offset. Phys assets go through the MMU
;             : window; decb assets follow their records.
;------------------------------------------------------------------------------
dmpkg_load
        pshs    b,x,y
        tst     dmpkg_live              ; is the stream already parked here?
        beq     @seek
        ldd     dmpkg_desc.blk,x
        cmpd    dmpkg_blk
        bne     @seek
        lda     dmpkg_desc.blk+2,x
        cmpa    dmpkg_blk+2
        bne     @seek
        ldd     dmpkg_desc.off,x
        cmpd    dmpkg_off
        beq     @here
@seek   pshs    u
        lda     dmpkg_desc.blk,x
        ldu     dmpkg_desc.blk+1,x
        jsr     dmpkg_start
        puls    u
        lbcs    @err
        ldd     dmpkg_desc.off,x        ; skip to the asset's offset (0-511)
        beq     @here
        std     dmpkg_off               ; stream position once skipped
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
        lda     dmpkg_desc.off+1,x
        bita    #1
        beq     @here
        lda     SDC_DATA                ; odd: drop the pair's 1st byte, so
@here                                   ; the next read is $FF4B
        lda     dmpkg_desc.flags,x
        bita    #DMPKG_F_DECB
        bne     @decb
        ldd     dmpkg_desc.len,x
        std     dmpkg_left
        lda     dmpkg_desc.len+2,x
        sta     dmpkg_left+2
        tst     dmpkg_desc.flags,x      ; DMPKG_F_PHYS is bit 7
        bmi     @phys
        pshs    u
        ldu     dmpkg_desc.dest+1,x
        jsr     dmpkg_copy
        puls    u
        bcs     @err
        bra     @ok
; CoCo 3 physical load through the MMU window
@phys   pshs    u
        ifdef   DMPKG_MMU_HOME
        lda     #DMPKG_MMU_HOME
        else
        lda     DMPKG_MMUREG
        endc
        sta     dmpkg_msave
        ldd     dmpkg_desc.dest,x       ; block = dest >> 13: A:B = bits 23-8,
        aslb                            ; shifted left 3 leaves bits 20-13 in A
        rola
        aslb
        rola
        aslb
        rola
        sta     dmpkg_mblk
        sta     DMPKG_MMUREG
        ldd     dmpkg_desc.dest+1,x     ; window offset = dest & $1FFF
        anda    #$1F
        addd    #DMPKG_WINDOW
        tfr     d,u
        sta     dmpkg_phys              ; nonzero (window is never in page 0)
        jsr     dmpkg_copy
        pshs    a,cc
        lda     dmpkg_msave
        sta     DMPKG_MMUREG
        clr     dmpkg_phys
        puls    a,cc
        puls    u
        bcs     @err
        bra     @ok
; decb records: [len.w addr.w data]... [0000 exec.w]
@decb   clr     dmpkg_left
@decb_loop
        jsr     dmpkg_getword           ; D = length
        bcs     @err
        std     dmpkg_left+1
        beq     @decb_end
        jsr     dmpkg_getword           ; D = load address
        bcs     @err
        tfr     d,u
        jsr     dmpkg_copy
        bcs     @err
        bra     @decb_loop
@decb_end
        jsr     dmpkg_getword           ; exec address
        bcs     @err
        tfr     d,u
@ok     andcc   #$fe                    ; clear carry (success)
        puls    b,x,y,pc
@err    jsr     dmpkg_fail
        puls    b,x,y,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_start
; Entry       : Reg A = block number bits 23-16
;             :     U = block number bits 15-0
; Exit        : CC.C clear: stream running, first block READY
;             : CC.C set  : A = error code
; Destroys    : A, CC
; Calls       : dmpkg_stop, dmpkg_waitidle, dmpkg_waitready
; Description : Stops any running stream, then starts a new one at the block
;             : and resets the stream position to offset 0.
;------------------------------------------------------------------------------
dmpkg_start
        jsr     dmpkg_stop
        sta     dmpkg_blk
        stu     dmpkg_blk+1
        sta     SDC_P1
        stu     SDC_DATA
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        bsr     dmpkg_waitidle
        bcs     @exit
        lda     #SDC_C_STREAM
        sta     SDC_STAT
        bsr     dmpkg_waitready
        bcs     @exit
        clr     dmpkg_off
        clr     dmpkg_off+1
        clr     dmpkg_poll
        inc     dmpkg_live
@exit   rts

;------------------------------------------------------------------------------
; Function    : dmpkg_settle
; Entry       : none
; Exit        : none
; Destroys    : CC
; Calls       : none
; Description : Delay loop of DMPKG_DELAY passes, so the SDC has ~20us after a
;             : command before its registers are read.
;------------------------------------------------------------------------------
dmpkg_settle
        pshs    x
        ldx     #DMPKG_DELAY
@loop   leax    -1,x
        bne     @loop
        puls    x,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_waitidle
; Entry       : none
; Exit        : CC.C clear: BUSY clear
;             : CC.C set  : A = DMPKG_ETIME
; Destroys    : A, CC
; Calls       : dmpkg_settle
; Description : Settles, then polls status until BUSY clears or the
;             : DMPKG_TIMEOUT x 65536 poll limit runs out.
;------------------------------------------------------------------------------
dmpkg_waitidle
        bsr     dmpkg_settle
        pshs    x
        lda     #DMPKG_TIMEOUT
        sta     dmpkg_tmo
        ldx     #0
@loop   lda     SDC_STAT
        bita    #SDC_BUSY
        beq     @ok
        leax    -1,x
        bne     @loop
        dec     dmpkg_tmo
        bne     @loop
        lda     #DMPKG_ETIME            ; ERROR CODE: BUSY timeout
        orcc    #1                      ; set carry (error)
        puls    x,pc
@ok     andcc   #$fe                    ; clear carry (success)
        puls    x,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_waitready
; Entry       : none
; Exit        : CC.C clear: READY set
;             : CC.C set  : A = DMPKG_EFAIL (FAILED, or BUSY dropped with
;             :             SDC_ERROR), DMPKG_EEOF (BUSY dropped, status $00:
;             :             end of file) or DMPKG_ETIME
; Destroys    : A, CC
; Calls       : dmpkg_settle
; Description : Settles, then polls status until READY is set, the controller
;             : fails, the stream ends, or the poll limit runs out.
;------------------------------------------------------------------------------
dmpkg_waitready
        bsr     dmpkg_settle
        pshs    x
        lda     #DMPKG_TIMEOUT
        sta     dmpkg_tmo
        ldx     #0
@loop   lda     SDC_STAT
        bmi     @fail
        bita    #SDC_READY
        bne     @ok
        bita    #SDC_BUSY
        beq     @eof
        leax    -1,x
        bne     @loop
        dec     dmpkg_tmo
        bne     @loop
        lda     #DMPKG_ETIME            ; ERROR CODE: READY timeout
        fcb     $8C                     ; skip 2 bytes
@fail   lda     #DMPKG_EFAIL            ; ERROR CODE: SDC reported FAILED
@err    orcc    #1                      ; set carry (error)
        puls    x,pc
@eof    bita    #SDC_ERROR              ; firmware ends a failed stream with
        bne     @fail                   ; $08 (no FAILED bit), a clean one $00
        lda     #DMPKG_EEOF             ; ERROR CODE: end of file
        bra     @err
@ok     andcc   #$fe                    ; clear carry (success)
        puls    x,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_getbyte
; Entry       : none
; Exit        : CC.C clear: A = next stream byte
;             : CC.C set  : A = error code
; Destroys    : A, CC
; Calls       : dmpkg_waitready, dmpkg_nextblk
; Description : Reads one byte from $FF4A (even offset) or $FF4B (odd offset),
;             : waiting for READY first at the start of each block.
;------------------------------------------------------------------------------
dmpkg_getbyte
        pshs    b,x
        tst     dmpkg_poll
        beq     @ready
        bsr     dmpkg_waitready
        bcs     @exit
        clr     dmpkg_poll
@ready  ldx     dmpkg_off
        tfr     x,d
        bitb    #1
        bne     @odd
        lda     SDC_DATA                ; even offset: first byte of the pair
        bra     @adv
@odd    lda     SDC_DATA+1              ; odd offset: second byte
@adv    leax    1,x
        cmpx    #512
        bne     @set_off
        bsr     dmpkg_nextblk
        ldx     #0
@set_off
        stx     dmpkg_off
        andcc   #$fe                    ; clear carry (success)
@exit   puls    b,x,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_getword
; Entry       : none
; Exit        : CC.C clear: D = next two stream bytes (big-endian)
;             : CC.C set  : A = error code
; Destroys    : A, B, CC
; Calls       : dmpkg_getbyte
; Description : Reads a 16-bit big-endian value from the stream.
;------------------------------------------------------------------------------
dmpkg_getword
        bsr     dmpkg_getbyte
        bcs     @error
        tfr     a,b                     ; B = 1st byte (dmpkg_getbyte keeps B)
        bsr     dmpkg_getbyte
        bcs     @error                  ; A = error code
        exg     a,b                     ; D = 1st:2nd
@error  rts

;------------------------------------------------------------------------------
; Function    : dmpkg_nextblk
; Entry       : none
; Exit        : none
; Destroys    : CC
; Calls       : none
; Description : Steps dmpkg_blk to the next block and flags that READY must be
;             : polled before its first byte is read.
;------------------------------------------------------------------------------
dmpkg_nextblk
        pshs    a
        inc     dmpkg_blk+2
        bne     @exit
        inc     dmpkg_blk+1
        bne     @exit
        inc     dmpkg_blk
@exit   lda     #1
        sta     dmpkg_poll
        puls    a,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_copy
; Entry       : Reg U = destination
;             : dmpkg_left  = bytes to copy (24-bit)
;             : dmpkg_phys  = nonzero if U is in the MMU window (dmpkg_mblk mapped)
; Exit        : CC.C clear: U advanced past the data
;             : CC.C set  : A = error code
; Destroys    : A, CC (U advanced)
; Calls       : dmpkg_waitready, dmpkg_nextblk
; Description : Copies stream bytes in chunks capped by the end of the block,
;             : the bytes left and, for phys loads, the end of the window (the
;             : next 8K is mapped when it fills). Inner loop: 32 bytes per
;             : pass, 16x LDD ,X / STD n,U.
;------------------------------------------------------------------------------
dmpkg_copy
        pshs    b,x,y
        ldx     #SDC_DATA
@loop   lda     dmpkg_left
        ora     dmpkg_left+1
        ora     dmpkg_left+2
        bne     @more
        andcc   #$fe                    ; clear carry (success)
@exit   puls    b,x,y,pc
@more   tst     dmpkg_poll              ; new block: wait for the buffer
        beq     @nopoll
        jsr     dmpkg_waitready
        bcs     @exit
        clr     dmpkg_poll
@nopoll tst     dmpkg_phys
        beq     @room
        cmpu    #DMPKG_WEND             ; window full: map the next 8K
        bne     @room
        inc     dmpkg_mblk
        lda     dmpkg_mblk
        sta     DMPKG_MMUREG
        ldu     #DMPKG_WINDOW
@room   ldd     #512                    ; chunk = bytes left in this block
        subd    dmpkg_off
        tst     dmpkg_left              ; capped by bytes left to copy
        bne     @cap_win
        cmpd    dmpkg_left+1
        bls     @cap_win
        ldd     dmpkg_left+1
@cap_win
        tst     dmpkg_phys              ; and by room left in the window
        beq     @set_chunk
        std     dmpkg_chunk
        ldd     #DMPKG_WEND
        pshs    u
        subd    ,s++
        cmpd    dmpkg_chunk
        bls     @set_chunk
        ldd     dmpkg_chunk
@set_chunk
        std     dmpkg_chunk
        tfr     d,y                     ; Y = bytes to copy now
        ldd     dmpkg_left+1            ; dmpkg_left -= chunk
        subd    dmpkg_chunk
        std     dmpkg_left+1
        bcc     @chk_odd
        dec     dmpkg_left
@chk_odd
        ldb     dmpkg_off+1             ; odd offset: pick up the 2nd byte first
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
        sta     dmpkg_n
        beq     @w2
        leau    16,u                    ; mid-group: offsets -16..14 fit 5 bits
; @w32: copy 32 bytes per pass, 16 x (ldd ,x / std n,u), then the next group
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
        dec     dmpkg_n
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
@adv    ldd     dmpkg_off               ; advance stream position
        addd    dmpkg_chunk
        cmpd    #512
        bne     @set_off
        jsr     dmpkg_nextblk
        ldd     #0
@set_off
        std     dmpkg_off
        jmp     @loop
