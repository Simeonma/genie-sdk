"""genie — SDK package root.

The public surface is ``f`` only: the worker pre-injects it on every exec; the root package **does not export model entry points**.
"""

#: Kept in sync with the root pyproject.toml in both places (release discipline).
__version__ = "1.0.0a4"
