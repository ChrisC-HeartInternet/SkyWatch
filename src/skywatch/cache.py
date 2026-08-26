"""Disk cache for raw API responses.

Every response is stored twice over, in effect:
- `latest` entries let re-runs inside the TTL skip the network entirely;
- timestamped history files accumulate, so past raw data is never lost.

Layout:
    cache/<source>/<key>.json                 latest response + metadata envelope
    cache/<source>/history/<key>_<UTC>.json   append-only history
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from skywatch import console


def _safe_key(key: str) -> str:
    """Filesystem-safe cache key; long keys get hashed for uniqueness."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_")
    if len(slug) > 80:
        digest = hashlib.sha256(key.encode()).hexdigest()[:12]
        slug = f"{slug[:60]}_{digest}"
    return slug


class DiskCache:
    def __init__(self, root: Path, ttl_minutes: int) -> None:
        self.root = root
        self.ttl_minutes = ttl_minutes

    def _path(self, source: str, key: str) -> Path:
        return self.root / source / f"{_safe_key(key)}.json"

    def get(self, source: str, key: str, *, refresh: bool = False) -> Any | None:
        """Return the cached payload if fresh enough, else None.

        refresh=True forces a miss so the caller refetches (history still accumulates).
        """
        if refresh:
            return None
        path = self._path(source, key)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text())
            fetched_at = datetime.fromisoformat(envelope["fetched_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            console.log().warning("Corrupt cache entry %s; refetching", path)
            return None
        age_min = (datetime.now(UTC) - fetched_at).total_seconds() / 60
        if age_min > self.ttl_minutes:
            return None
        console.log().debug("cache hit: %s/%s (age %.0f min)", source, key, age_min)
        return envelope["payload"]

    def put(self, source: str, key: str, payload: Any) -> None:
        """Store a payload as latest and append it to history."""
        now = datetime.now(UTC)
        envelope = {"fetched_at": now.isoformat(), "source": source, "key": key, "payload": payload}
        text = json.dumps(envelope, ensure_ascii=False)

        path = self._path(source, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

        hist_dir = path.parent / "history"
        hist_dir.mkdir(exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        (hist_dir / f"{_safe_key(key)}_{stamp}.json").write_text(text)

    def prune_history(self, *, keep_days: int) -> int:
        """Delete history files older than keep_days. Returns the count removed.

        With several snapshots a day the grid source alone writes ~1 MB each;
        raw history is for debugging, not the archive of record (state.db is).
        """
        cutoff = datetime.now(UTC).timestamp() - keep_days * 86400
        removed = 0
        for f in self.root.glob("*/history/*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
