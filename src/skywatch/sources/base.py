"""The Source protocol every data source implements.

Adding a new source: write one module in sources/ with a class exposing
`name`, `fetch(refresh=...)` returning a parsed pydantic payload, and add it
to the registry in sources/__init__.py. Raw responses go through DiskCache so
history accumulates and re-runs stay polite.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    """A data source. Construction wires in config + cache; fetch() does the work."""

    name: str

    def fetch(self, *, refresh: bool = False) -> Any:
        """Fetch (or reuse cached) data and return the parsed, validated payload."""
        ...
