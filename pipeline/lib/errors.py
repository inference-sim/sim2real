"""Shared exception types for the pipeline library.

Housing common exceptions here breaks import cycles: low-level modules
(e.g. ``slicer``) can raise errors that higher-level modules
(e.g. ``assemble_run``) also use, without importing each other.
"""


class AssembleError(Exception):
    """Raised when assembly fails validation."""
