"""Skywatch command line interface.

The application is a one-shot run by design: no internal scheduler. Use launchd
(see launchd/) to invoke `skywatch run` on a schedule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from skywatch import console
from skywatch.config import load_config

app = typer.Typer(
    name="skywatch",
    help="Local weather-watching and analysis. Physics models forecast; the LLM narrates.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to config.yaml (default: repo root)"),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output only; no chatter")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")]
RefreshOpt = Annotated[bool, typer.Option("--refresh", help="Ignore cache TTL and refetch")]
NoLLMOpt = Annotated[bool, typer.Option("--no-llm", help="Skip the LLM; write the digest only")]
RunDirOpt = Annotated[
    Path | None, typer.Option("--run", help="Run directory to brief (default: latest)")
]


@app.command()
def run(
    config: ConfigOpt = None,
    json_out: JsonOpt = False,
    verbose: VerboseOpt = False,
    no_llm: NoLLMOpt = False,
    refresh: RefreshOpt = False,
) -> None:
    """Full cycle: fetch, compute features, brief, alert, write outputs."""
    console.setup(json_out, verbose)
    cfg = load_config(config)
    from skywatch.pipeline import run_cycle

    run_cycle(cfg, use_llm=not no_llm, refresh=refresh, json_out=json_out)


@app.command()
def snapshot(
    config: ConfigOpt = None,
    json_out: JsonOpt = False,
    verbose: VerboseOpt = False,
    refresh: RefreshOpt = False,
) -> None:
    """Fetch + features + dashboard + state, NO LLM. Carries the last briefing forward."""
    console.setup(json_out, verbose)
    cfg = load_config(config)
    from skywatch.pipeline import snapshot_only

    snapshot_only(cfg, refresh=refresh, json_out=json_out)


@app.command()
def watch(config: ConfigOpt = None, verbose: VerboseOpt = False) -> None:
    """Daemon: snapshot when a model publishes a new cycle; brief at anchors or on change."""
    console.setup(False, verbose)
    cfg = load_config(config)
    from skywatch.watch import main as watch_main

    watch_main(cfg)


@app.command()
def fetch(
    config: ConfigOpt = None,
    json_out: JsonOpt = False,
    verbose: VerboseOpt = False,
    refresh: RefreshOpt = False,
) -> None:
    """Fetch data only. Populates the on-disk cache; computes nothing."""
    console.setup(json_out, verbose)
    cfg = load_config(config)
    from skywatch.pipeline import fetch_only

    fetch_only(cfg, refresh=refresh, json_out=json_out)


@app.command()
def brief(
    config: ConfigOpt = None,
    json_out: JsonOpt = False,
    verbose: VerboseOpt = False,
    run_dir: RunDirOpt = None,
) -> None:
    """LLM only: re-brief from an existing digest without refetching."""
    console.setup(json_out, verbose)
    cfg = load_config(config)
    from skywatch.pipeline import brief_only

    brief_only(cfg, run_dir=run_dir, json_out=json_out)


@app.command()
def status(
    config: ConfigOpt = None,
    json_out: JsonOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Show the latest alerts, ENSO state and polar vortex state."""
    console.setup(json_out, verbose)
    cfg = load_config(config)
    from skywatch.pipeline import show_status

    show_status(cfg, json_out=json_out)


@app.command()
def serve(
    config: ConfigOpt = None,
    verbose: VerboseOpt = False,
    host: Annotated[str | None, typer.Option("--host", help="Override serve.host")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Override serve.port")] = None,
) -> None:
    """Serve output/ over HTTP (Tailscale by default): dashboard, history, alerts."""
    console.setup(False, verbose)
    cfg = load_config(config)
    from skywatch.serve import resolve_host, serve_forever

    serve_forever(
        cfg.output_dir,
        resolve_host(host or cfg.serve.host),
        port or cfg.serve.port,
    )


@app.command()
def stormwatch(config: ConfigOpt = None, verbose: VerboseOpt = False) -> None:
    """Real-time lightning watcher: strike map + ntfy alerts near home."""
    console.setup(False, verbose)
    cfg = load_config(config)
    from skywatch.stormwatch import main as stormwatch_main

    stormwatch_main(cfg)


@app.command(name="open")
def open_dashboard(config: ConfigOpt = None, verbose: VerboseOpt = False) -> None:
    """Open the latest dashboard in the default browser."""
    console.setup(False, verbose)
    cfg = load_config(config)
    from skywatch.pipeline import open_latest_dashboard

    open_latest_dashboard(cfg)


if __name__ == "__main__":
    app()
