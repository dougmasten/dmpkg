# Original disks

Put the three Rick Dangerous disk images here. They're not in the repo, and git ignores everything in this folder except this file.

| file | SHA-256 |
|---|---|
| `RICK1.DSK` | `92c5af8b0d96d205ab2999da3e79488f0f5de8bd165d021762412a31e8f87c6e` |
| `RICK2.DSK` | `fed0e62379ad0315a7ce37a227365bfe4e70fed7d6e58b6b6428b57ab7d360bf` |
| `RICK3.DSK` | `e37290da24297b750db1634e5e2bb24965cd775449d1a8e937d1568193d866ad` |

`extract.py` (`make extract`, or `make all`) checks each disk against its SHA-256 and stops if one differs, so a different dump or release can't build a broken image. To check them yourself: `sha256sum RICK*.DSK`.
