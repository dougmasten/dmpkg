"""
Building a project: manifest -> placed assets -> image (header, TOC, data)
-> DECB disk (if asked) -> image file and include files. The command line is
in dmpkg.cli; the pieces are in manifest, layout, image, decb and outputs.
"""
from __future__ import annotations

import os

from . import decb, image, layout, manifest, outputs


def build(manifest_path: str, verbose=False, loader=True) -> dict:
    """Build the project a manifest describes and write its outputs (paths
    relative to the manifest): image, and if given, equates, table and map.
    loader=False leaves a disk's loader off: the first pass of a two-pass
    build, which only needs the include files."""
    project, assets = manifest.load(manifest_path, loader)

    # ---- placement and size
    toc_bytes, toc_blocks = image.toc_size(len(assets))
    data0 = toc_blocks * image.BLOCK
    end = layout.place(assets, data0, project.disk)
    size = layout.image_size(end, project.min_size, project.disk)
    blocks = size // image.BLOCK

    # ---- the image: data, then header and TOC in block 0
    img = bytearray([project.pad_byte]) * size
    for a in assets:
        img[a.pos:a.pos + len(a.data)] = a.data
    toc = image.toc(assets)
    build_id = image.build_id(toc)
    hdr = image.header(project.name, len(assets), build_id, blocks, toc_blocks, project.disk)
    img[0:toc_bytes] = hdr + toc
    img[toc_bytes:data0] = bytes(data0 - toc_bytes)

    # ---- the DECB file system, around the dmpkg data
    disk_files = []
    if project.disk:
        used = [(0, data0)] + [(a.pos, len(a.data)) for a in assets]
        files = []
        if project.loader:
            files.append(project.loader)
            if project.autoexec:
                files.append(decb.autoexec(*project.loader[:2]))
        disk_files = decb.write_disk(img, used, files)

    # ---- write it all
    def out(p):
        """Output path relative to the manifest, its folder created."""
        path = project.rel(p)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return path

    out_image = out(project.image)
    with open(out_image, "wb") as f:
        f.write(img)
    for p, lines in ((project.equates, outputs.equates(project, assets, build_id, blocks)),
                     (project.table, outputs.table(project, assets, build_id))):
        if p:
            with open(out(p), "w", encoding="ascii", newline="\n") as f:
                f.write("\n".join(lines).rstrip("\n") + "\n")
    report = outputs.report(project, assets, build_id, size, end, toc_blocks, disk_files)
    if project.map:
        with open(out(project.map), "w", encoding="utf-8") as f:
            f.write("\n".join(report) + "\n")
    if verbose:
        print("\n".join(report))
    return {"image": out_image, "assets": assets, "build_id": build_id, "blocks": blocks,
            "mount": project.mount, "header": hdr, "disk_files": disk_files}
