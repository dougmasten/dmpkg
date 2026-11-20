"""
The project manifest (docs/MANIFEST.md): reading the YAML, checking every
key, and turning it into a Project and its list of Assets.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

import yaml

from . import decb
from .errors import PackError
from .image import MIN_IMAGE, Asset

KNOWN_PROJECT = {"title", "image", "sdc_path", "equates", "table", "map", "prefix",
                 "min_size", "pad_byte", "disk"}
KNOWN_DISK = {"loader", "autoexec", "tokenize", "reserve", "data"}
KNOWN_ASSET = {"name", "file", "type", "load", "phys", "align"}


@dataclass
class Project:
    path: str                 # the manifest file
    base: str                 # folder its relative paths start from
    title: str                # stored in the image header
    image: str                # output image, as the manifest gives it
    mount: str | None         # SDC path to mount by, None: already mounted (disk)
    prefix: str
    min_size: int
    pad_byte: int
    disk: bool                # the image is also a DECB disk
    loader: tuple | None      # (name, ext, bytes) of the LOADM file put on it
    autoexec: bool
    tokenize: bool            # AUTOEXEC.BAS tokenized (else ASCII)
    reserve: int              # granules kept free for the loader and AUTOEXEC.BAS
    data: tuple               # (name, ext) of the file that owns the dmpkg data
    equates: str | None       # output paths, as the manifest gives them
    table: str | None
    map: str | None

    def rel(self, p: str) -> str:
        """Path relative to the manifest's folder."""
        return os.path.join(self.base, p)


# ---------------------------------------------------------------- values

def num(v, what: str) -> int:
    """Accept YAML ints or strings like $4000, 0x4000, &H4000, %1010, 1234."""
    if isinstance(v, bool):
        raise PackError(f"{what}: expected a number, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace("_", "")
        try:
            if s.startswith("$"):
                return int(s[1:], 16)
            if s[:2].lower() == "&h":
                return int(s[2:], 16)
            if s.startswith("%"):
                return int(s[1:], 2)
            return int(s, 0)
        except ValueError:
            pass
    raise PackError(f"{what}: can't read {v!r} as a number")


def sym(name: str) -> str:
    """Asset name -> assembler symbol: upper case, other characters '_'."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name).upper()
    if not s or s[0].isdigit():
        s = "_" + s
    return s


def sdc_name(path: str) -> str:
    """'GAMES/mygame.sds' -> 'GAMES/MYGAME.SDS' (SDC path: 8.3 names, upper-cased)."""
    path = path.replace("\\", "/").strip("/")
    *dirs, leaf = path.split("/")
    base, _, ext = leaf.rpartition(".")
    if not base:
        base, ext = ext, ""
    if len(base) > 8 or len(ext) > 3 or not base:
        raise PackError(f"sdc_path: '{leaf}' is not a valid 8.3 name")
    for d in dirs:
        if len(d) > 8:
            raise PackError(f"sdc_path: directory '{d}' longer than 8 characters")
    leaf = base + ("." + ext if ext else "")
    out = "/".join(dirs + [leaf]).upper()
    if len(out) + 2 > 255:
        raise PackError("sdc_path too long")
    return out


# ---------------------------------------------------------------- the file

def load(path: str, loader: bool = True) -> tuple[Project, list[Asset]]:
    """Read and check a manifest; the assets come back unplaced. With
    loader=False the disk's loader isn't read (first pass of a two-pass
    build, before it's assembled)."""
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or "project" not in doc or "assets" not in doc:
        raise PackError(f"{path}: needs top-level 'project' and 'assets' keys")
    proj, raw_assets = doc["project"], doc["assets"]
    if not isinstance(proj, dict):
        raise PackError("project: must be a mapping")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise PackError("assets: must be a non-empty list")
    bad = set(proj) - KNOWN_PROJECT
    if bad:
        raise PackError(f"project: unknown key(s) {sorted(bad)}")
    project = _project(path, proj, loader)
    seen = set()
    return project, [_asset(project, i, a, seen) for i, a in enumerate(raw_assets)]


def _project(path: str, proj: dict, with_loader: bool) -> Project:
    base = os.path.dirname(os.path.abspath(path))
    title = str(proj.get("title", "PROJECT"))
    image = proj.get("image")
    if not image:
        raise PackError("project.image is required (e.g. MYGAME.SDS)")
    disk = proj.get("disk", False)
    if disk is True:
        disk = {}
    if disk is not False and not isinstance(disk, dict):
        raise PackError("project.disk: must be true, false or a mapping")
    if isinstance(disk, dict):
        bad = set(disk) - KNOWN_DISK
        if bad:
            raise PackError(f"project.disk: unknown key(s) {sorted(bad)}")
    # a disk image is normally already mounted (the loader came off it), so
    # it has no mount name unless sdc_path asks for one
    if "sdc_path" in proj or disk is False:
        mount = sdc_name(str(proj.get("sdc_path", os.path.basename(image))))
    else:
        mount = None
    prefix = str(proj.get("prefix", ""))
    min_size = num(proj.get("min_size", MIN_IMAGE), "project.min_size")
    if min_size < MIN_IMAGE:
        raise PackError(f"project.min_size ${min_size:X} is below ${MIN_IMAGE:X}: "
                        "the SDC won't mount a smaller image")
    pad_byte = num(proj.get("pad_byte", 0 if disk is False else 0xFF),
                   "project.pad_byte") & 0xFF

    loader, autoexec, tokenize, reserve, data = None, False, True, 0, ("DMPKG", "DAT")
    if disk is not False:
        reserve = num(disk.get("reserve", 0), "project.disk.reserve")
        if "data" in disk:
            data = decb.file_name(str(disk["data"]), "project.disk.data")
        if "loader" in disk:
            lpath = str(disk["loader"])
            if with_loader:
                loader = _loader(base, lpath)
            else:
                decb.file_name(lpath, "project.disk.loader")    # still check the name
        autoexec = disk.get("autoexec", "loader" in disk)
        if not isinstance(autoexec, bool):
            raise PackError("project.disk.autoexec must be true or false")
        if autoexec and "loader" not in disk:
            raise PackError("project.disk.autoexec needs a loader to run")
        tokenize = disk.get("tokenize", True)
        if not isinstance(tokenize, bool):
            raise PackError("project.disk.tokenize must be true or false")
        taken = {decb.file_name(str(disk["loader"]), "")} if "loader" in disk else set()
        if autoexec:
            taken.add(("AUTOEXEC", "BAS"))
        if data in taken:
            raise PackError(f"project.disk.data: {'.'.join(data)} is already "
                            "the loader or AUTOEXEC.BAS")

    return Project(path=path, base=base, title=title, image=image, mount=mount,
                   prefix=prefix, min_size=min_size, pad_byte=pad_byte,
                   disk=disk is not False, loader=loader, autoexec=autoexec, tokenize=tokenize,
                   reserve=reserve, data=data,
                   equates=proj.get("equates"), table=proj.get("table"),
                   map=proj.get("map"))


def _loader(base: str, lpath: str) -> tuple | None:
    """(name, ext, bytes) of the loader, or None if it isn't built yet."""
    lname = decb.file_name(lpath, "project.disk.loader")
    try:
        with open(os.path.join(base, lpath), "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        # first pass of a two-pass build: the loader is assembled against
        # the include files this run writes
        print(f"dmpkg: note: {lpath} not found yet; disk written without it",
              file=sys.stderr)
        return None
    except OSError as e:
        raise PackError(f"project.disk.loader: {e}")
    decb.parse_loadm(raw, lpath)            # must be a LOADM file
    return (*lname, raw)


def _asset(project: Project, i: int, a, seen: set) -> Asset:
    """Check one asset entry and read its file; seen holds the names so far."""
    where = f"assets[{i}]"
    if not isinstance(a, dict):
        raise PackError(f"{where}: must be a mapping")
    bad = set(a) - KNOWN_ASSET
    if bad:
        raise PackError(f"{where}: unknown key(s) {sorted(bad)}")
    if "name" not in a or "file" not in a:
        raise PackError(f"{where}: 'name' and 'file' are required")
    nm = sym(str(a["name"]))
    if nm in seen:
        raise PackError(f"{where}: duplicate asset name {nm}")
    seen.add(nm)
    where = f"asset {nm}"
    kind = str(a.get("type", "raw")).lower()
    if kind not in ("raw", "decb"):
        raise PackError(f"{where}: type must be raw or decb")
    if "load" in a and "phys" in a:
        raise PackError(f"{where}: give either load or phys, not both")
    align = a.get("align", False)
    if not isinstance(align, bool):
        raise PackError(f"{where}: align must be true or false")
    try:
        with open(project.rel(str(a["file"])), "rb") as f:
            raw = f.read()
    except OSError as e:
        raise PackError(f"{where}: {e}")

    asset = Asset(name=nm, src=str(a["file"]), kind=kind, align=align)
    if kind == "decb":
        if "load" in a or "phys" in a:
            raise PackError(f"{where}: decb assets load to their own segment addresses")
        asset.segs, asset.exec_addr = decb.parse_loadm(raw, asset.src)
        asset.data = decb.records(asset.segs, asset.exec_addr)
    else:
        if not raw:
            raise PackError(f"{where}: file is empty")
        asset.data = raw
        if "phys" in a:
            asset.phys = True
            asset.dest = num(a["phys"], f"{where}.phys")
            if asset.dest + len(asset.data) > 0x200000:
                raise PackError(f"{where}: phys ${asset.dest:06X}+{len(asset.data)} "
                                "runs past 2MB")
        elif "load" in a:
            asset.dest = num(a["load"], f"{where}.load")
            if asset.dest + len(asset.data) > 0x10000:
                raise PackError(f"{where}: load ${asset.dest:04X}+{len(asset.data)} "
                                "runs past $FFFF (use phys: for CoCo 3 RAM)")
        else:
            raise PackError(f"{where}: raw assets need load: or phys:")
    if len(asset.data) >= 1 << 24:
        raise PackError(f"{where}: over 16MB")
    return asset
