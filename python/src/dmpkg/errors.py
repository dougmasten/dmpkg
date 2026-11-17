"""The exception dmpkg raises for input it can't use."""


class PackError(Exception):
    """A manifest, input file or image dmpkg can't use; the message says why."""
