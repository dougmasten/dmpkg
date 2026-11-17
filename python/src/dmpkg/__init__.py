"""dmpkg - project disc packer for the CoCo SDC STREAM protocol."""

__version__ = "0.1.0"

from .pack import PackError, build, read_image  # noqa: E402

__all__ = ["PackError", "build", "read_image", "__version__"]
