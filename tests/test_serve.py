"""Serve layer: Tailscale detection parsing and HTTP routes (real server, ephemeral port)."""

import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from skywatch.serve import _Handler, cgnat_addresses, resolve_host

IFCONFIG_SAMPLE = """
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
	inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	inet 192.168.1.23 netmask 0xffffff00 broadcast 192.168.1.255
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
	inet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
"""


def test_cgnat_detection() -> None:
    assert cgnat_addresses(IFCONFIG_SAMPLE) == ["100.101.102.103"]
    # 100.128.x is outside 100.64.0.0/10 and must not match
    assert cgnat_addresses("\tinet 100.128.0.1 netmask 0xffffffff\n") == []
    assert cgnat_addresses("\tinet 10.0.0.5 netmask 0xffffff00\n") == []


def test_resolve_host_passthrough() -> None:
    assert resolve_host("0.0.0.0") == "0.0.0.0"
    assert resolve_host("192.168.1.9") == "192.168.1.9"


def _start_server(root: Path) -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.socket.getsockname()
    return httpd, f"http://{host}:{port}"


def _get(url: str, follow: bool = True) -> tuple[int, str, dict[str, str]]:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # type: ignore[no-untyped-def]
            return None

    opener = (urllib.request.build_opener() if follow
              else urllib.request.build_opener(NoRedirect))
    def _norm(headers: object) -> dict[str, str]:
        return {k.title(): v for k, v in dict(headers).items()}  # type: ignore[call-overload]

    try:
        with opener.open(url, timeout=5) as r:
            return r.status, r.read().decode(), _norm(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", _norm(e.headers)


def test_routes(tmp_path: Path) -> None:
    run = tmp_path / "2026-01-05_0630"
    run.mkdir()
    (run / "dashboard.html").write_text("<title>dash</title>")
    (run / "alerts.json").write_text('{"alerts": []}')
    (tmp_path / "latest").symlink_to(run.name)

    httpd, base = _start_server(tmp_path)
    try:
        # root serves the latest dashboard DIRECTLY (no redirect round trip)
        status, body, headers = _get(f"{base}/", follow=False)
        assert status == 200 and "dash" in body
        assert headers.get("Cache-Control") == "no-cache"
        # stable alias for agents, also direct
        status, body, _ = _get(f"{base}/alerts.json", follow=False)
        assert status == 200 and body == '{"alerts": []}'
        # run history listing
        status, body, _ = _get(f"{base}/runs")
        assert status == 200 and "2026-01-05_0630" in body
        # immutable caching for timestamped (never-rewritten) run paths
        status, _, headers = _get(f"{base}/2026-01-05_0630/alerts.json")
        assert status == 200
        assert "immutable" in headers.get("Cache-Control", "")
        # ...but never for the moving latest/ view of the same file
        _, _, headers = _get(f"{base}/latest/alerts.json")
        assert headers.get("Cache-Control") == "no-cache"
        # missing file 404s
        status, _, _ = _get(f"{base}/latest/nope.txt")
        assert status == 404
    finally:
        httpd.shutdown()


def test_gzip_and_conditional(tmp_path: Path) -> None:
    import gzip as gz

    run = tmp_path / "2026-01-05_0630"
    run.mkdir()
    big = "<title>dash</title>" + "x" * 5000
    (run / "dashboard.html").write_text(big)
    (run / "tiny.json").write_text('{"a":1}')
    (tmp_path / "latest").symlink_to(run.name)

    httpd, base = _start_server(tmp_path)
    try:
        # gzip when the client accepts it
        req = urllib.request.Request(f"{base}/", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.headers["Content-Encoding"] == "gzip"
            assert r.headers["Vary"] == "Accept-Encoding"
            raw = r.read()
            assert len(raw) < len(big)
            assert gz.decompress(raw).decode() == big
            last_modified = r.headers["Last-Modified"]
        # identity when the client doesn't
        status, body, headers = _get(f"{base}/")
        assert status == 200 and body == big
        assert "Content-Encoding" not in headers
        # files below the size floor are not compressed
        req = urllib.request.Request(
            f"{base}/latest/tiny.json", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert "Content-Encoding" not in r.headers
        # conditional requests still 304 on the gzip path
        req = urllib.request.Request(f"{base}/", headers={
            "Accept-Encoding": "gzip", "If-Modified-Since": last_modified})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 304
    finally:
        httpd.shutdown()


def test_head_mirrors_get(tmp_path: Path) -> None:
    run = tmp_path / "2026-01-05_0630"
    run.mkdir()
    (run / "dashboard.html").write_text("<title>dash</title>")
    (tmp_path / "latest").symlink_to(run.name)
    httpd, base = _start_server(tmp_path)
    try:
        req = urllib.request.Request(f"{base}/", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert int(r.headers["Content-Length"]) == len("<title>dash</title>")
            assert "text/html" in r.headers["Content-Type"]
    finally:
        httpd.shutdown()


def test_markdown_and_json_content_types(tmp_path: Path) -> None:
    run = tmp_path / "2026-01-05_0630"
    run.mkdir()
    (run / "briefing.md").write_text("# hi")
    (run / "alerts.json").write_text("{}")
    (tmp_path / "latest").symlink_to(run.name)
    httpd, base = _start_server(tmp_path)
    try:
        _, _, headers = _get(f"{base}/latest/briefing.md")
        assert headers["Content-Type"].startswith("text/markdown")
        _, _, headers = _get(f"{base}/latest/alerts.json")
        assert headers["Content-Type"].startswith("application/json")
    finally:
        httpd.shutdown()
