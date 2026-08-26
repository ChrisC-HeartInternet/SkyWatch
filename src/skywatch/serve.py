"""Serve the output/ tree over HTTP — designed for Tailscale access.

Stdlib only: a threaded static-file server with three conveniences on top:
- /                 -> the latest run's dashboard
- /alerts.json etc. -> that file from the latest run (stable URLs for Hermes)
- /runs             -> browsable listing of all past runs

By default it binds to this machine's Tailscale address (auto-detected from
the CGNAT 100.64.0.0/10 range) so the app is reachable across the tailnet but
not exposed on the LAN or beyond.
"""

from __future__ import annotations

import email.utils
import gzip
import http.server
import ipaddress
import os
import re
import subprocess
from functools import partial
from pathlib import Path

from skywatch import console

_TS_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_INET_RE = re.compile(r"^\s*inet (\d+\.\d+\.\d+\.\d+)", re.MULTILINE)

# Latest-run files worth a stable top-level URL. Served directly (an internal
# rewrite, not a redirect) so remote visitors and polling agents don't pay an
# extra round trip per request.
_LATEST_ALIASES = {"/alerts.json", "/digest.json", "/briefing.md", "/dashboard.html"}

# Text formats worth compressing; everything the app produces is one of these.
_COMPRESSIBLE = {".html", ".json", ".md", ".txt", ".svg", ".css", ".js"}
_GZIP_MIN_BYTES = 256

# Timestamped run directories are immutable once written; only latest/ moves.
_RUN_PATH_RE = re.compile(r"^/\d{4}-\d{2}-\d{2}_\d{4}/")


def cgnat_addresses(ifconfig_text: str) -> list[str]:
    """All CGNAT-range (Tailscale) IPv4 addresses in ifconfig output."""
    return [
        ip for ip in _INET_RE.findall(ifconfig_text)
        if ipaddress.ip_address(ip) in _CGNAT
    ]


def detect_tailscale_ip() -> str | None:
    """This machine's Tailscale IPv4, via the app CLI or interface scan."""
    # NB: the GUI app CLI exits 0 even on failure (prints an error message
    # instead of an IP); the ip_address() parse below rejects that case.
    for cmd in ([_TS_APP_CLI, "ip", "-4"], ["tailscale", "ip", "-4"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            first = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
            if out.returncode == 0 and first and ipaddress.ip_address(first) in _CGNAT:
                return first
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    # Absolute path: launchd's PATH usually lacks /sbin (hit live).
    for ifconfig in ("/sbin/ifconfig", "ifconfig"):
        try:
            out = subprocess.run([ifconfig], capture_output=True, text=True, timeout=5)
            addrs = cgnat_addresses(out.stdout)
            if addrs:
                return addrs[0]
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def resolve_host(configured: str) -> str:
    """Turn the configured host into a bind address.

    'tailscale' auto-detects; anything else (0.0.0.0, an IP, a name) passes through.
    """
    if configured != "tailscale":
        return configured
    ip = detect_tailscale_ip()
    if ip is None:
        console.log().warning(
            "No Tailscale address found; binding to 127.0.0.1 only. "
            "Set serve.host in config.yaml to override."
        )
        return "127.0.0.1"
    return ip


# Tiny gzip cache keyed by (path, mtime, size): the dashboard and alerts are
# re-requested far more often than they change (Hermes polls; launchd rewrites
# twice a day), so each version compresses exactly once.
_gzip_cache: dict[tuple[str, float, int], bytes] = {}
_GZIP_CACHE_MAX = 32


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static files from output/: latest-run aliases, gzip, tuned caching."""

    # Markdown displays in the browser instead of downloading as a blob.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".md": "text/markdown; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }

    def _rewrite_path(self) -> str:
        """Apply the alias rewrites; returns the original request path."""
        path = self.path.partition("?")[0]
        if path == "/":
            self.path = "/latest/dashboard.html"
        elif path in _LATEST_ALIASES:
            self.path = f"/latest{path}"
        return path

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        query = self.path.partition("?")[2]
        path = self._rewrite_path()
        if path in ("/runs", "/runs/") or (path == "/" and query == "runs"):
            self.path = "/"
            f = self.list_directory(self.directory)
            if f:
                try:
                    self.copyfile(f, self.wfile)
                finally:
                    f.close()
            return
        if self._maybe_send_gzipped():
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 (stdlib naming)
        # curl -I and health checks must see the same resource GET would serve.
        path = self._rewrite_path()
        if path in ("/runs", "/runs/"):
            self.path = "/"
        super().do_HEAD()

    def _accepts_gzip(self) -> bool:
        accept = self.headers.get("Accept-Encoding", "")
        return "gzip" in accept.lower()

    def _maybe_send_gzipped(self) -> bool:
        """Serve a compressible file gzipped. Returns False to fall back."""
        if not self._accepts_gzip() or "Range" in self.headers:
            return False
        fs_path = Path(self.translate_path(self.path))
        if fs_path.suffix.lower() not in _COMPRESSIBLE or not fs_path.is_file():
            return False
        try:
            st = os.stat(fs_path)
        except OSError:
            return False
        if st.st_size < _GZIP_MIN_BYTES:
            return False

        # Honour If-Modified-Since exactly as the stdlib path would.
        ims = self.headers.get("If-Modified-Since")
        if ims:
            try:
                since = email.utils.parsedate_to_datetime(ims).timestamp()
                if int(st.st_mtime) <= int(since):
                    self.send_response(304)
                    self.end_headers()
                    return True
            except (TypeError, ValueError, OverflowError):
                pass

        key = (str(fs_path), st.st_mtime, st.st_size)
        body = _gzip_cache.get(key)
        if body is None:
            body = gzip.compress(fs_path.read_bytes(), compresslevel=6)
            if len(_gzip_cache) >= _GZIP_CACHE_MAX:
                _gzip_cache.clear()
            _gzip_cache[key] = body

        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(fs_path)))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", self.date_time_string(int(st.st_mtime)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def end_headers(self) -> None:
        # Timestamped run dirs are immutable once written; everything else —
        # latest/, aliases, listings — retargets between requests, so browsers
        # and Hermes must revalidate (no-cache still allows 304s).
        if _RUN_PATH_RE.match(self.path or ""):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Vary", "Accept-Encoding")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        console.log().debug("serve: %s", format % args)


def serve_forever(output_dir: Path, host: str, port: int) -> None:
    """Run the HTTP server until interrupted."""
    output_dir.mkdir(parents=True, exist_ok=True)
    handler = partial(_Handler, directory=str(output_dir))
    with http.server.ThreadingHTTPServer((host, port), handler) as httpd:
        bound = httpd.socket.getsockname()
        console.err().print(
            f"[green]Serving[/green] {output_dir} at http://{bound[0]}:{bound[1]}/  "
            f"(dashboard at /, history at /runs, latest alerts at /alerts.json)"
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            console.err().print("\n[yellow]Server stopped.[/yellow]")
