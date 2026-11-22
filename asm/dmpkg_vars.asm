;------------------------------------------------------------------------------
; dmpkg_vars.asm - the dmpkg runtime's state (17 bytes, all rmb)
;
; Part of dmpkg; see dmpkg.asm for the copyright and license.
;
; dmpkg.asm includes this file, which puts the state right after the code.
; To put it somewhere else, such as the direct page, include it yourself
; before dmpkg.asm:
;
;         setdp   $00             ; the page DP holds whenever dmpkg runs
;         org     $00E0
;         include "dmpkg_vars.asm"
;
; The first include defines DMPKG_VARS, so the one in dmpkg.asm then adds
; nothing. Don't define DMPKG_VARS yourself: the state would be left out.
;
; lwasm uses direct addressing for every access whose address falls in the
; setdp page, so DP must hold that page whenever a dmpkg routine runs.
; Nothing here needs a starting value: dmpkg_init sets up what it must.
;------------------------------------------------------------------------------

        ifndef  DMPKG_VARS
DMPKG_VARS      equ     1       ; state defined: later includes add nothing

dmpkg_blk       rmb     3       ; block holding the next stream byte
dmpkg_off       rmb     2       ; offset of the next byte in that block
dmpkg_poll      rmb     1       ; nonzero: wait READY before the next byte
dmpkg_left      rmb     3       ; bytes still to copy (24-bit)
dmpkg_chunk     rmb     2       ; bytes in the current copy chunk
dmpkg_mblk      rmb     1       ; block mapped in the window
dmpkg_msave     rmb     1       ; window block before the load
dmpkg_tmo       rmb     1       ; timeout outer counter
; set up by dmpkg_init with two std: keep these pairs together and in order
dmpkg_live      rmb     1       ; nonzero while a stream is running
dmpkg_phys      rmb     1       ; nonzero only during a phys copy
dmpkg_ver       rmb     2       ; firmware version (BCD), set by dmpkg_init

        endc
