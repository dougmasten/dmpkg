"""dmpkg - project image packer for the CoCo SDC STREAM protocol."""

__version__ = "0.1.0"

from .decb import read_disk  # noqa: E402
from .errors import PackError  # noqa: E402
from .image import read_image  # noqa: E402
from .pack import build  # noqa: E402

__all__ = ["PackError", "build", "read_disk", "read_image", "__version__"]
