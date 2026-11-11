"""genie — SDK package root.

The public surface is the pre-injected ``genie`` handle only (Q15): the
worker pre-injects it on every exec; the root package **does not export
model entry points**.
"""

#: Kept in sync with the root pyproject.toml in both places (release discipline).
__version__ = "2.0.1a1"
