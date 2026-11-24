;------------------------------------------------------------------------------
; dmpkg.asm - CoCo SDC STREAM loader for dmpkg images
;
; Copyright (c) 2026 Doug Masten
;
; This software is provided 'as-is', without any express or implied
; warranty. In no event will the authors be held liable for any damages
; arising from the use of this software.
;
; Permission is granted to anyone to use this software for any purpose,
; including commercial applications, and to alter it and redistribute it
; freely, subject to the following restrictions:
;
; 1. The origin of this software must not be misrepresented; you must not
;    claim that you wrote the original software. If you use this software
;    in a product, an acknowledgment in the product documentation would be
;    appreciated but is not required.
; 2. Altered source versions must be plainly marked as such, and must not be
;    misrepresented as being the original software.
; 3. This notice may not be removed or altered from any source distribution.
;
;------------------------------------------------------------------------------
;
; 6809 source (runs on HD6309 in either mode), lwasm syntax.
; Pair with the files dmpkg generates:  equates (EQUs) and table
; (mount name, build_id, 12-byte asset descriptors).
;
; Entry points
;   dmpkg_init   Checks a CoCo SDC is present (DMPKG_ENODEV if not), enters
;                command mode (ending a running stream), gets its firmware
;                version: D = BCD (e.g. $0124 for 124), also in dmpkg_ver (0
;                if the firmware predates the VERSION command, before 113),
;                and checks it's at least DMPKG_MINVER (DMPKG_EVER if not).
;                Sets up the runtime's state: call it once, at start.
;   dmpkg_open   X = mount name ("M:..." zero-terminated, e.g. DMPKG_MountName),
;                    or 0 to use the image already in DMPKG_DRIVE (a dmpkg disk
;                    image the loader was run from)
;                Y = expected 4-byte build_id (DMPKG_BuildID) or 0 to skip
;                    the check
;                Enters command mode (ending any running stream), mounts the
;                image, starts a stream at block 0 and checks the header's
;                build_id so a stale or wrong image is caught. Needs
;                dmpkg_init first.
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
;   DMPKG_EHDR    4  build_id in block 0 doesn't match (stale or wrong
;                    image)
;   DMPKG_EEOF    5  stream ended (BUSY dropped) before the data was read
;   DMPKG_ETIME   6  controller didn't answer in time
;   DMPKG_EFAIL   7  controller reported an error (FAILED, or the stream ended
;                    with status $08: card error)
; On any error the stream is stopped. Registers other than A/CC are
; preserved, except B for dmpkg_init (it returns D) and U
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
;   DMPKG_DELAY    12     settle loop before polling READY (8 cyc/pass: ~54us at
;                         1.79MHz; the SDC needs ~20us to update READY; 1-65535)
;   DMPKG_TIMEOUT  16     poll timeout, units of 65536 polls (1-255)
;   DMPKG_MINVER   $0117  lowest firmware dmpkg_init accepts (117 added STREAM)
;
; State: 17 bytes in dmpkg_vars.asm, placed after the code. To put them
; elsewhere (e.g. the direct page), include dmpkg_vars.asm before this file.
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
DMPKG_MINVER    equ     $0117           ; First version with the stream I/O command
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
DMPKG_EHDR      equ     4               ; build_id mismatch
DMPKG_EEOF      equ     5               ; stream ended before the data
DMPKG_ETIME     equ     6               ; controller didn't answer in time
DMPKG_EFAIL     equ     7               ; controller reported an error

; asset descriptor flags (dmpkg_desc.flags)
DMPKG_F_PHYS    equ     %10000000       ; dest is a CoCo 3 physical address
DMPKG_F_DECB    equ     %01000000       ; data is DECB records (exec in U)

; asset descriptor (12 bytes, as dmpkg generates them in the table)
dmpkg_desc      struct
blk             rmb     3               ; block where the data starts
off             rmb     2               ; byte offset in that block (0-511)
len             rmb     3               ; stored length
flags           rmb     1               ; DMPKG_F_PHYS, DMPKG_F_DECB
dest            rmb     3               ; CPU or physical address
                endstruct

;------------------------------------------------------------------------------
; state: here, unless the program included dmpkg_vars.asm already
        include "dmpkg_vars.asm"

;------------------------------------------------------------------------------
; Function    : dmpkg_init
; Entry       : none
; Exit        : CC.C clear: D = firmware version in BCD ($0124 = 124),
;             :             at least DMPKG_MINVER
;             : CC.C set  : A = DMPKG_ENODEV, DMPKG_EVER or DMPKG_ETIME
; Destroys    : A, B, CC
; Calls       : dmpkg_cmdmode, dmpkg_waitidle
; Description : Checks a CoCo SDC is present, then gets its firmware version
;             : (also kept in dmpkg_ver) and checks it supports STREAM
;             : (VERSION >= DMPKG_MINVER; firmware before 113 has no VERSION
;             : command and counts as 0). The presence check writes the Flash
;             : data register ($FF42) and reads the Flash control register
;             : ($FF43), whose bits 5-6 follow it on an SDC; without one, it
;             : returns DMPKG_ENODEV before any command is written. If found,
;             : the controller is left in command mode (dmpkg_close leaves it).
;             : It also sets up the runtime's state (all rmb): call it once,
;             : at start, before any other call.
;------------------------------------------------------------------------------
dmpkg_init
; initialize variables
        clra                            ; no stream known to be running,
        clrb
        std     dmpkg_live              ; not in a phys copy (dmpkg_phys),
        std     dmpkg_ver               ; no version yet
; check if a CoCo SDC is present
        lda     #$64                    ; Flash data: bits 5-6 set
        sta     SDC_FDATA
        lda     SDC_FCTL
        clr     SDC_FDATA               ; Flash data: bits 5-6 clear
        eora    SDC_FCTL                ; bits that followed the data
        cmpa    #$60
        bne     @nosdc                  ; no CoCo SDC was found
; get CoCo SDC version
        jsr     dmpkg_cmdmode           ; enter command mode (end any running stream)
        bcs     @exit
        lda     #'V
        sta     SDC_P1
        lda     #SDC_C_QUERY            ; VERSION ($C0, 'V' in $FF49)
        sta     SDC_STAT
        jsr     dmpkg_waitidle
        bcs     @exit
        tst     SDC_STAT                ; FAILED: no VERSION command (< 113),
        bmi     @old                    ; dmpkg_ver stays 0
        ldd     SDC_DATA                ; D = firmware version in BCD
        std     dmpkg_ver
        cmpd    #DMPKG_MINVER
        bhs     @exit                   ; new enough (carry clear)
@old    lda     #DMPKG_EVER             ; ERROR CODE: firmware < 117
        fcb     $8C                     ; skip 2 bytes
@nosdc  lda     #DMPKG_ENODEV           ; ERROR CODE: no CoCo SDC
@error  orcc    #1                      ; set carry (error)
@exit   rts

;------------------------------------------------------------------------------
; Function    : dmpkg_open
; Entry       : Reg X = mount name ("M:..." zero-terminated, e.g. DMPKG_MountName),
;             :         or 0 to stream the image already in DMPKG_DRIVE
;             :     Y = expected 4-byte build_id (DMPKG_BuildID), or 0 to skip it
; Exit        : CC.C clear: image mounted, stream parked after the build_id
;             : CC.C set  : A = error code (stream stopped)
; Destroys    : A, CC
; Calls       : dmpkg_cmdmode, dmpkg_waitidle,
;             : dmpkg_waitready, dmpkg_start, dmpkg_fail
; Description : Enters command mode (ending any running stream), mounts the
;             : image (unless X = 0), starts a stream at block 0 and compares
;             : the header's build_id (bytes 4-7) so a stale image is caught.
;             : dmpkg_init must have succeeded first (it checks the SDC and
;             : its firmware, and sets up the state).
;------------------------------------------------------------------------------
dmpkg_open
        pshs    b,x,y,u
        jsr     dmpkg_cmdmode           ; ends any stream, enters command mode
        bcs     @error
        leax    ,x                      ; X = 0: no mount, the image is
        beq     @mount_ok               ; already in DMPKG_DRIVE
        lda     #SDC_C_MOUNT
        sta     SDC_STAT
        jsr     dmpkg_waitready
        bcs     @error
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
        bcs     @error
        lda     SDC_STAT
        bpl     @mount_ok
        lda     #DMPKG_EMOUNT           ; ERROR CODE: file not found
        bra     @error
@mount_ok
        clra                            ; start stream at block 0
        ldu     #0
        jsr     dmpkg_start
        bcs     @error
        ldy     3,s                     ; expected build_id pointer (saved Y)
        beq     @ok
; The firmware needs 3.6us to refill the pair after a $FF4B read, and nothing
; makes the CPU wait. Back-to-back LDDs come 6 cycles apart (5 on a 6309 in
; native mode), 3.4us at 1.79MHz, so each is spaced out with a MUL (1 byte,
; 11 cycles; D is a throwaway there). CMPD/BNE space out the last pair.
        ldd     SDC_DATA                ; drop bytes 0-3 (magic: the
        mul                             ;  build_id check covers it)
        ldd     SDC_DATA
        mul
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
@ehdr   lda     #DMPKG_EHDR             ; ERROR CODE: wrong or stale image
@error  jsr     dmpkg_fail
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
        lda     dmpkg_live              ; is stream running?
        beq     @exit                   ; no, exit
        clr     dmpkg_live              ; clear stream running flag
        lda     #SDC_C_ABORT
        sta     SDC_STAT                ; abort stream
        jsr     dmpkg_waitidle          ; wait
@exit   andcc   #$fe                    ; clear carry (success)
        puls    a,pc

;------------------------------------------------------------------------------
; Function    : dmpkg_close
; Entry       : none
; Exit        : CC.C clear
; Destroys    : CC
; Calls       : dmpkg_stop
; Description : Stops any stream, then returns the controller to floppy
;             : emulation mode.
;------------------------------------------------------------------------------
dmpkg_close
        bsr     dmpkg_stop              ; stop stream
        clr     SDC_LATCH               ; controller to floppy emulation mode
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
        ldb     dmpkg_live              ; is the stream already parked here?
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
        lbcs    @error
        ldd     dmpkg_desc.off,x        ; skip to the asset's offset (0-511)
        beq     @here
        std     dmpkg_off               ; stream position once skipped
        lsra                            ; B = whole pairs to drop (A = 0:
        rorb                            ; offset < 512), Z if none
        beq     @skip_odd
@skip_loop
        ldy     SDC_DATA                ; drop a pair: $FF4A then $FF4B
        decb
        bne     @skip_loop
@skip_odd
        lda     dmpkg_desc.off+1,x
        lsra                            ; bit 0 (odd offset) into carry
        bcc     @here
        lda     SDC_DATA                ; odd: drop the pair's 1st byte, so
@here                                   ; the next read is $FF4B
        lda     dmpkg_desc.flags,x
        bita    #DMPKG_F_DECB
        bne     @decb
        ldd     dmpkg_desc.len,x
        std     dmpkg_left
        lda     dmpkg_desc.len+2,x
        sta     dmpkg_left+2
        lda     dmpkg_desc.flags,x      ; DMPKG_F_PHYS is bit 7
        bmi     @phys
        ldx     dmpkg_desc.dest+1,x
        jsr     dmpkg_copy
        bcs     @error
        bra     @ok
; CoCo 3 physical load through the MMU window
@phys   lda     DMPKG_MMUREG            ; window block before the load, put
        sta     dmpkg_msave             ; back after it
        ldd     dmpkg_desc.dest,x       ; block = dest >> 13: A:B = bits 23-8,
        lslb                            ; shifted left 3 leaves bits 20-13 in A
        rola
        lslb
        rola
        lslb
        rola
        sta     dmpkg_mblk
        sta     DMPKG_MMUREG
        ldd     dmpkg_desc.dest+1,x     ; window offset = dest & $1FFF
        anda    #$1F
        addd    #DMPKG_WINDOW
        tfr     d,x
        sta     dmpkg_phys              ; nonzero (window is never in page 0)
        jsr     dmpkg_copy
        pshs    a,cc
        lda     dmpkg_msave
        sta     DMPKG_MMUREG
        clr     dmpkg_phys
        puls    a,cc
        bcs     @error
        bra     @ok
; decb records: [len.w addr.w data]... [0000 exec.w]
@decb   clr     dmpkg_left
@decb_loop
        jsr     dmpkg_getword           ; D = length
        bcs     @error
        std     dmpkg_left+1
        beq     @decb_end
        jsr     dmpkg_getword           ; D = load address
        bcs     @error
        tfr     d,x
        jsr     dmpkg_copy
        bcs     @error
        bra     @decb_loop
@decb_end
        jsr     dmpkg_getword           ; exec address
        bcs     @error
        tfr     d,u
@ok     andcc   #$fe                    ; clear carry (success)
        puls    b,x,y,pc
@error  jsr     dmpkg_fail
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
; Function    : dmpkg_cmdmode
; Entry       : none
; Exit        : CC.C clear: in command mode, controller idle, no stream
;             : CC.C set  : A = DMPKG_ETIME
; Destroys    : A, CC
; Calls       : dmpkg_settle, dmpkg_waitidle
; Description : Enters command mode. A stream still running (BUSY set) is
;             : aborted ($D0) by its BUSY bit, and dmpkg_live cleared.
;             : Falls through into dmpkg_waitidle, which must follow it.
;------------------------------------------------------------------------------
dmpkg_cmdmode
        clr     dmpkg_live
        lda     #SDC_CMDMODE
        sta     SDC_LATCH
        pshs    x
        bsr     dmpkg_settle
        puls    x
        lda     SDC_STAT
        lsra                            ; SDC_BUSY (bit 0) into carry: a stream
        bcc     dmpkg_waitidle          ; (or command) still running?
        lda     #SDC_C_ABORT
        sta     SDC_STAT
;       bra     dmpkg_waitidle          ; falls through: dmpkg_waitidle is next

;------------------------------------------------------------------------------
; Function    : dmpkg_waitidle
; Entry       : none
; Exit        : CC.C clear: BUSY clear
;             : CC.C set  : A = DMPKG_ETIME
; Destroys    : A, CC
; Calls       : dmpkg_settle
; Description : Settles, then polls status until BUSY clears or the
;             : DMPKG_TIMEOUT x 65536 poll limit runs out. Leaves through
;             : dmpkg_waitready's exits (dmpkg_wok, dmpkg_wtime).
;------------------------------------------------------------------------------
dmpkg_waitidle
        pshs    x
        bsr     dmpkg_settle            ; X = 0, timeout set
@loop   lda     SDC_STAT
        lsra                            ; SDC_BUSY (bit 0) into carry
        bcc     dmpkg_wok
        leax    -1,x
        bne     @loop
        dec     dmpkg_tmo
        bne     @loop
        bra     dmpkg_wtime

;------------------------------------------------------------------------------
; Function    : dmpkg_settle
; Entry       : none
; Exit        : X = 0 and dmpkg_tmo = DMPKG_TIMEOUT: the poll counters
;             : dmpkg_waitidle and dmpkg_waitready start from
; Destroys    : A, X, CC
; Calls       : none
; Description : Delay loop of DMPKG_DELAY passes, so the SDC has ~20us after a
;             : command before its registers are read.
;------------------------------------------------------------------------------
dmpkg_settle
        ldx     #DMPKG_DELAY
@loop   leax    -1,x
        bne     @loop                   ; X = 0 for the caller's poll count
        lda     #DMPKG_TIMEOUT
        sta     dmpkg_tmo
        rts

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
        pshs    x
        bsr     dmpkg_settle            ; X = 0, timeout set
@loop   lda     SDC_STAT
        bmi     @fail
        bita    #SDC_READY
        bne     dmpkg_wok
        lsra                            ; SDC_BUSY (bit 0) into carry
        bcc     @eof                    ; BUSY clear: the stream ended
        leax    -1,x
        bne     @loop
        dec     dmpkg_tmo
        bne     @loop
dmpkg_wtime
        lda     #DMPKG_ETIME            ; ERROR CODE: READY timeout
        fcb     $8C                     ; skip 2 bytes
@fail   lda     #DMPKG_EFAIL            ; ERROR CODE: SDC reported FAILED
@error  orcc    #1                      ; set carry (error)
        puls    x,pc
@eof    bita    #SDC_ERROR/2            ; firmware ends a failed stream with
        bne     @fail                   ; $08 (no FAILED bit), a clean one $00
        lda     #DMPKG_EEOF             ; ERROR CODE: end of file
        bra     @error
dmpkg_wok
        andcc   #$fe                    ; clear carry (success)
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
        lda     dmpkg_poll
        beq     @ready
        bsr     dmpkg_waitready
        bcs     @exit
        clr     dmpkg_poll
@ready  ldx     dmpkg_off
        tfr     x,d
        lsrb                            ; offset bit 0 into carry
        bcs     @odd
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
;             : polled before its first byte is read (dmpkg_poll nonzero:
;             : it's always cleared before the next block, so INC is enough).
;------------------------------------------------------------------------------
dmpkg_nextblk
        inc     dmpkg_poll              ; nonzero: wait READY first
        inc     dmpkg_blk+2
        bne     @exit
        inc     dmpkg_blk+1
        bne     @exit
        inc     dmpkg_blk
@exit   rts

;------------------------------------------------------------------------------
; Function    : dmpkg_copy
; Entry       : Reg X = destination
;             : dmpkg_left  = bytes to copy (24-bit)
;             : dmpkg_phys  = nonzero if X is in the MMU window (dmpkg_mblk mapped)
; Exit        : CC.C clear: X advanced past the data
;             : CC.C set  : A = error code
; Destroys    : A, CC (X advanced)
; Calls       : dmpkg_waitready, dmpkg_nextblk
; Description : Copies stream bytes in chunks capped by the end of the block,
;             : the bytes left and, for phys loads, the end of the window (the
;             : next 8K is mapped when it fills). X is the destination,
;             : Y points at the data registers and U carries the data, which
;             : leaves A to count the passes of the inner loop: 32 bytes per
;             : pass, 16x LDU ,Y / STU n,X.
;------------------------------------------------------------------------------
dmpkg_copy
        pshs    b,y,u
        ldy     #SDC_DATA
@loop   lda     dmpkg_left              ; any of the 24-bit count left?
        ora     dmpkg_left+1            ; (OR its three bytes: zero only
        ora     dmpkg_left+2            ; when all of them are)
        bne     @more                   ; yes: copy another chunk
        andcc   #$fe                    ; clear carry (success)
@exit   puls    b,y,u,pc
@more   lda     dmpkg_poll              ; new block: wait for the buffer
        beq     @nopoll
        jsr     dmpkg_waitready
        bcs     @exit
        clr     dmpkg_poll
@nopoll lda     dmpkg_phys
        beq     @room
        cmpx    #DMPKG_WEND             ; window full: map the next 8K
        bne     @room
        inc     dmpkg_mblk
        lda     dmpkg_mblk
        sta     DMPKG_MMUREG
        ldx     #DMPKG_WINDOW
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
        pshs    x
        subd    ,s++
        cmpd    dmpkg_chunk
        bls     @set_chunk
        ldd     dmpkg_chunk
@set_chunk
        std     dmpkg_chunk
        ldd     dmpkg_left+1            ; dmpkg_left -= chunk
        subd    dmpkg_chunk
        std     dmpkg_left+1
        bcc     @chk_odd
        dec     dmpkg_left
@chk_odd
        ldb     dmpkg_off+1             ; odd offset: pick up the 2nd byte first
        lsrb
        bcc     @whole
        lda     1,y
        sta     ,x+
        ldd     dmpkg_chunk             ; D = bytes left to copy now
        subd    #1
        bra     @split
@whole  ldd     dmpkg_chunk
@split  pshs    b                       ; bits 0-4: the 0-31 byte tail
        lslb                            ; A = D/32 = passes (D < 1024, so
        rola                            ; D<<3 >> 8)
        lslb
        rola
        lslb
        rola
        beq     @w2
        ldb     #16                     ; X = mid-group: offsets -16..14 fit
        abx                             ; 5 bits
        lslb                            ; B = 32 for ABX
; Partially unrolled transfer loop (32 bytes per iteration)
@w32    ldu     ,y
        stu     -16,x
        ldu     ,y
        stu     -14,x
        ldu     ,y
        stu     -12,x
        ldu     ,y
        stu     -10,x
        ldu     ,y
        stu     -8,x
        ldu     ,y
        stu     -6,x
        ldu     ,y
        stu     -4,x
        ldu     ,y
        stu     -2,x
        ldu     ,y
        stu     ,x
        ldu     ,y
        stu     2,x
        ldu     ,y
        stu     4,x
        ldu     ,y
        stu     6,x
        ldu     ,y
        stu     8,x
        ldu     ,y
        stu     10,x
        ldu     ,y
        stu     12,x
        ldu     ,y
        stu     14,x
        abx
        deca
        bne     @w32
        leax    -16,x                   ; X back to the next free byte
@w2     ldb     ,s                      ; pairs in the tail
        andb    #%00011110
        beq     @w1
        lsrb
@w2loop ldu     ,y
        stu     ,x++
        decb
        bne     @w2loop
@w1     puls    b
        lsrb                            ; odd tail: 1st byte of the next pair
        bcc     @adv
        lda     ,y
        sta     ,x+
@adv    ldd     dmpkg_off               ; advance stream position
        addd    dmpkg_chunk
        cmpd    #512
        bne     @set_off
        jsr     dmpkg_nextblk
        clra
        clrb
@set_off
        std     dmpkg_off
        jmp     @loop
