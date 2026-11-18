# dmpkg project manifest (`project.yaml`)

A manifest is a YAML file that tells `dmpkg build` what goes into an image: the project settings, and a list of the assets to pack. Relative paths in it, for both inputs and outputs, are relative to the folder that holds the manifest, not to the current directory.

```sh
dmpkg build path/to/project.yaml -v
```

## A minimal manifest

```yaml
project:
  image: build/GAME.SDS
  equates: build/assets.inc
  table: build/assettab.asm

assets:
  - name: title
    file: title.bin
    load: 0x4000
```

This builds a bare stream image that the program mounts as `M:GAME.SDS` (the image's file name, since no `sdc_path` is given), plus the two include files the program assembles against.

## A full manifest

```yaml
project:
  name: Demo                       # stored in the image header (16 characters)
  image: build/DEMO.DSK            # output image (required)
  sdc_path: GAMES/DEMO.DSK         # mount by this path (optional with disk:)
  equates: build/assets.inc        # lwasm include: EQUs only
  table: build/assettab.asm        # lwasm include: mount name, build_id, descriptors
  map: build/DEMO.map              # layout report
  prefix: ""                       # prepended to every generated symbol
  min_size: 0x14400                # smallest image written (default and minimum)
  pad_byte: 0xFF                   # fill for unused bytes ($00, or $FF with disk:)
  disk:                            # also make the image a DECB disk
    loader: build/LOADER.BIN       # LOADM file put on the disk
    autoexec: true                 # AUTOEXEC.BAS that runs it
    reserve: 0                     # granules kept free for those two
    data: DMPKG.DAT                # DECB file that owns the asset granules

assets:
  - name: font
    file: assets/font.bin
    load: "$6000"                  # CPU address
  - name: title
    file: assets/title.gfx
    phys: 0x60001                  # CoCo 3 physical address
    align: true                    # start on a fresh 512-byte block
  - name: code
    file: assets/code.bin
    type: decb                     # LOADM file: segments go to their own addresses
```

`example/project.yaml` is a working manifest for a single-file disk.

## Top level

| key | required | meaning |
|---|---|---|
| `project` | yes | mapping of project settings, below |
| `assets` | yes | non-empty list of assets, in the order they're packed |

Other top-level keys are ignored, so you can keep your own data (YAML anchors, notes) beside them.

## `project`

| key | default | meaning |
|---|---|---|
| `image` | **required** | Output image file. Use `.SDS` for a bare stream image and `.DSK` for a disk image; dmpkg doesn't care, but people and emulators do. |
| `name` | `PROJECT` | Project name, stored in the header as ASCII. Only the first 16 characters are kept, and non-ASCII characters become `?`. `dmpkg list` shows it. |
| `sdc_path` | the image's file name | Where the image lives on the SD card, for the mount name (see [Mount name](#mount-name)). With `disk:` it's optional, and leaving it out means no mount at all. |
| `equates` | none | Path of the equates include (EQUs only). Optional, but programs need it. |
| `table` | none | Path of the table include (mount name, build_id, descriptors). Optional, but programs need it. |
| `map` | none | Path of a human-readable layout report. |
| `prefix` | `""` | Text put in front of every generated symbol (`DMPKG_MountName`, `DMPKG_BuildID`, `DMPKG_BUILD_ID`, `FONT`, `FONT_BLK`…). Use it to keep two images' symbols apart in one program. |
| `min_size` | `0x14400` | The image is padded to at least this many bytes (rounded up to a whole 512-byte block). It can't be lower than `0x14400` (82,944 bytes), the smallest disk image the SDC's `M:` mount accepts according to the User Guide. A disk image is always at least 161,280 bytes anyway. |
| `pad_byte` | `0`, or `0xFF` with `disk:` | Value for unused bytes: gaps between assets, the end of the last block, padding up to `min_size`. Only the low 8 bits are used. The TOC padding in block 0 is always `$00`. |
| `disk` | `false` | `true`, `false`, or a mapping (below). Anything but `false` makes the image a 35-track DECB disk as well. `true` is the same as an empty mapping: a disk with no loader on it. |

Output folders (for `image`, `equates`, `table` and `map`) are created if they don't exist.

## `project.disk`

| key | default | meaning |
|---|---|---|
| `loader` | none | LOADM (DECB binary) file to put on the disk, under its own file name. The name must be a valid DECB 8.3 name (letters, digits and `_$#!&@%^()'{}~-`); it's upper-cased on the disk. The file must be a well-formed LOADM file. |
| `autoexec` | `true` with a `loader`, else `false` | Also write `AUTOEXEC.BAS` (ASCII BASIC: `10 LOADM"LOADER.BIN":EXEC`, with the loader's name), which SDC-DOS runs at power-on. Needs a `loader`. |
| `reserve` | `0` | Granules (2,304 bytes each) kept free just before track 17, so the loader and `AUTOEXEC.BAS` still fit when the assets would fill the disk. Placement only depends on the manifest, so both passes of a two-pass build get the same layout. A loader under 2,304 bytes plus `AUTOEXEC.BAS` needs `2`. |
| `data` | `DMPKG.DAT` | Name of the DECB file that owns the granules holding the header, TOC and assets (what `DIR` shows). It must be a valid 8.3 name, and can't be the loader's name or `AUTOEXEC.BAS`. Only the directory entry changes: the layout and build_id stay the same. |

The loader is built in two passes. `dmpkg build --no-loader` leaves the loader (and `AUTOEXEC.BAS`) off the disk; the loader is assembled against the include files that pass writes, then a plain `dmpkg build` puts it on the disk. Without `--no-loader`, a missing `loader` file isn't an error either: dmpkg prints a note and writes the disk without it. Asset placement and the build_id don't depend on the loader, so the second pass writes the same include files. See [DISK.md](DISK.md).

## `assets`

Each entry is a mapping:

| key | default | meaning |
|---|---|---|
| `name` | **required** | Asset name. It becomes the symbol of the asset's descriptor (see [Symbols](#symbols)), so names must be unique once converted. |
| `file` | **required** | Source file. |
| `type` | `raw` | `raw` (the file's bytes as they are) or `decb` (a LOADM file), in any case. |
| `load` | | CPU address to load a raw asset to, `$0000–$FFFF`. The whole asset must fit below `$10000`. |
| `phys` | | CoCo 3 physical address to load a raw asset to, `$000000–$1FFFFF`, through the runtime's MMU window. The whole asset must fit below 2MB. |
| `align` | `false` | Start the asset on a fresh 512-byte block (offset 0). |

- **Raw assets** need exactly one of `load` or `phys`, and can't be empty.
- **DECB assets** take neither: each segment loads to the address in the file. The file must be a well-formed LOADM file (`$00` segment records, then a `$FF` postamble with the exec address). It's stored re-encoded as the records `dmpkg_load` follows, and `dmpkg_load` returns the exec address in U. Empty segments are dropped.
- **Size:** an asset's stored size must be under 16MB.

### Layout

Assets are placed **first fit, in manifest order**: each takes the first free space it fits in after the header and TOC, at any byte offset. So assets normally follow each other back to back, and a program that loads them in image order gets them from one stream with no restart.

`align: true` starts an asset on a fresh block instead. The gap that leaves stays free, and a later asset small enough to fit goes into it, so the image order can differ from the manifest order. On a disk image, no asset crosses track 17 (the directory track), and first fit uses the space left before it in the same way. The map report and `dmpkg list` show where each asset went.

## Numbers

Addresses and sizes can be YAML integers or strings:

| form | example |
|---|---|
| YAML integer | `24576`, `0x6000`, `0b110` |
| `$` hex | `"$6000"` |
| `&H` hex (BASIC) | `"&H6000"` |
| `%` binary | `"%0110000000000000"` |
| any of these with `_` separators | `"$7A_001"` |

An unquoted `$6000` works too, because YAML reads it as a string. `true`/`false` aren't accepted as numbers.

Watch out for leading zeros: YAML reads an unquoted `010000` as octal, which is `$1000`, not 10,000. Write decimal without the zero, or use one of the hex forms.

## Symbols

An asset's `name` becomes a symbol by upper-casing it and changing anything that isn't a letter, digit or `_` into `_`. A name that starts with a digit gets a `_` in front. `font` → `FONT`, `level-1` → `LEVEL_1`, `2nd map` → `_2ND_MAP`. Two names that convert to the same symbol are an error.

For each asset, the include files define (with `prefix` in front):

| symbol | file | meaning |
|---|---|---|
| `NAME` | table | 12-byte descriptor, passed in X to `dmpkg_load` |
| `NAME_IDX` | equates | index in the manifest (0 = first) |
| `NAME_BLK` | equates | block where the data starts |
| `NAME_OFF` | equates | byte offset in that block |
| `NAME_LEN` | equates | stored length (for decb, the record stream) |
| `NAME_DEST` | equates | raw assets: load address (CPU or physical) |
| `NAME_EXEC` | equates | decb assets: exec address |

and once per image:

| symbol | file | meaning |
|---|---|---|
| `DMPKG_MountName` | table | mount string (`fcn 'M:…'`), or `equ 0` for a disk image with no `sdc_path` |
| `DMPKG_BuildID` | table | the image's 4-byte build_id, for `dmpkg_open` to check |
| `DMPKG_BUILD_ID` | equates | the same build_id as a number |
| `DMPKG_IMAGE_BLOCKS` | equates | image size in 512-byte blocks |
| `DMPKG_ASSET_COUNT` | equates | number of assets |
| `DMPKG_DESC_SIZE` | equates | descriptor size (12) |

## Mount name

Without `disk:`, the program mounts the image by name with `dmpkg_open`, so the image must be on the card at `sdc_path`. The mount name is `M:` plus the path:

- `\` becomes `/`, and leading or trailing `/` are dropped
- every part is upper-cased; each folder name is at most 8 characters, and the file name is 8.3
- `games/demo.sds` → `M:GAMES/DEMO.SDS`

With `disk:` and no `sdc_path`, there's no mount name: `DMPKG_MountName` is 0, and `dmpkg_open` streams the disk already mounted in `DMPKG_DRIVE`, which is the one the loader was run from. Give `sdc_path` too if a program should mount the disk by name instead.

## Checks

`dmpkg build` stops with an error, and writes nothing, if:

- `project` or `assets` is missing, `assets` is empty, or either has the wrong type
- `project`, `project.disk` or an asset has a key it doesn't know (catches typos)
- `image` is missing, or `min_size` is below `0x14400`
- a number can't be read
- `sdc_path`, the loader's name or `data` isn't a valid 8.3 name, or `data` is the loader's name or `AUTOEXEC.BAS`
- `autoexec` is set without a `loader`, or the loader isn't a LOADM file
- an asset lacks `name` or `file`, two names give the same symbol, or `type` is neither `raw` nor `decb`
- a raw asset is empty, has both or neither of `load`/`phys`, or runs past `$FFFF` (`load`) or 2MB (`phys`)
- a decb asset has `load` or `phys`, or its file is malformed
- an asset file can't be read, or is 16MB or more
- the TOC is too big (more than 255 blocks) or runs into a disk's directory track
- a disk has no granules left for the loader or `AUTOEXEC.BAS` ("disk full"; see `reserve`)
- `reserve` asks for more granules than there are between the TOC and track 17
- the image would be larger than the 24-bit block range
