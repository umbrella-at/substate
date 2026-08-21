"""Subscription lifecycle for Python.

The public API is intentionally small. See README.md for the shape it will take.
"""

from substate.clock import Clock, FrozenClock, SystemClock

__all__ = [
    "Clock",
    "FrozenClock",
    "SystemClock",
    "__version__",
]

__version__ = "0.1.0.dev0"
