# Skywatch

A local weather-watching and analysis system. It pulls freely available weather and
climate data on a schedule, computes the derived quantities a human forecaster would
look at — ensemble spread, inter-model disagreement, anomalies against climatology,
forecast drift, ENSO state, stratospheric polar vortex health — and hands a compact
digest to a local LLM, which writes a plain-English briefing and structured alerts.

## AI as analyst, not oracle

The design rule that shapes everything here: **all forecasting comes from the physics
models in the data; the LLM is an analyst reading instruments, not a crystal ball.**

- Every number is computed in Python before the LLM sees anything. The LLM receives
  a ~1-page digest of pre-computed features and is forbidden (by system prompt *and*
  by validation) from inventing numbers or doing arithmetic.
- Alert *facts* are decided by deterministic threshold code. The LLM only phrases and
  groups them; if it fails or is unreachable, the same facts are rendered mechanically,
  so `alerts.json` is always schema-valid and arithmetically trustworthy.
- The LLM being down degrades the run (briefing carries an "unavailable" note) but
  never fails it — data, alerts, and dashboard are all produced regardless.

## What a run produces

```
output/2026-08-21_0630/
├── briefing.md       human-readable briefing (headline, 7 days, uncertainty,
│                     global drivers, what to watch)
├── alerts.json       validated alerts for other agents (see Hermes below)
├── digest.json       the exact digest sent to the LLM, for auditability
└── dashboard.html    self-contained page of charts — open it in any browser,
                      works offline, no external assets
output/latest/        symlink to the most recent run
state.db              SQLite history of every run's forecasts (feeds drift charts)
cache/                raw API responses, TTL'd latest + append-only history
```

## Quick start (Docker)

```bash
git clone https://github.com/ChrisC-HeartInternet/SkyWatch.git skywatch && cd skywatch
cp .env.example .env        # set SKYWATCH_LOCATION and your LLM endpoint
docker compose up -d        # scheduler (06:30 + 16:30), dashboard server, storm watch
docker compose run --rm --entrypoint skywatch scheduler run   # run a cycle now
open http://127.0.0.1:8092/
```

Three containers from one image: `scheduler` runs the twice-daily cycle,
`serve` hosts the dashboard, `stormwatch` watches lightning. Data persists in
the `skywatch-data` volume.

Reaching the dashboard over Tailscale from Docker: on a **Linux host** set
`SERVE_BIND_IP` to your Tailscale IP. **Docker Desktop (Mac/Windows) cannot
bind published ports to the Tailscale interface** — leave the loopback default
and run `tailscale serve --bg --http=8092 http://127.0.0.1:8092`, which fronts
it tailnet-only at `http://<machine-name>:8092/` (MagicDNS hostname; the raw
Tailscale IP is not routed by `tailscale serve`). Or set
`SERVE_BIND_IP=0.0.0.0` to answer on every host interface, LAN included.
The image is multi-arch (amd64/arm64); if `DOCKER_DEFAULT_PLATFORM` is set in
your shell, builds follow it.

## Quick start (native, macOS)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
git clone https://github.com/ChrisC-HeartInternet/SkyWatch.git ~/skywatch && cd ~/skywatch
cp .env.example .env        # set SKYWATCH_LOCATION and your LLM endpoint
uv sync
uv run skywatch run         # first full cycle (fetches ~30y climatology once)
uv run skywatch open        # dashboard in your browser
```

Scheduling and the always-on pieces use launchd — see *Scheduling* and
*Serving over Tailscale* below. Native binding to a Tailscale interface is
direct (`SKYWATCH_SERVE_HOST=tailscale` auto-detects it).

## Configuration: `.env` + `config.yaml`

Two layers, one rule — **environment wins**:

| | Holds | Tracked in git? |
|---|---|---|
| `.env` | *personal & deployment*: location, LLM endpoint/models, ntfy topic, bind address, data dir | no (`.env.example` is) |
| `config.yaml` | *tunables*: thresholds, model panel, vortex/climatology settings, radii | yes, generic |

`SKYWATCH_LOCATION` accepts a **UK postcode** (resolved via postcodes.io, which
also supplies the friendly name), a **place name** (Open-Meteo geocoding,
worldwide, supplies the timezone), or **`lat,lon`**. Both services are free and
keyless; the answer is cached so restarts work offline. Every variable is
documented in [`.env.example`](.env.example).

The LLM is any OpenAI-compatible endpoint (Ollama, LM Studio, a proxy). Ollama
needs no key; set `SKYWATCH_LLM_API_KEY` if yours does.

## CLI

| Command | Does |
|---|---|
| `skywatch run` | full cycle: fetch → features → digest → LLM → outputs → state |
| `skywatch fetch` | data only; populates the cache, computes nothing |
| `skywatch brief` | LLM only, over the latest (or `--run DIR`) existing digest |
| `skywatch status` | latest alerts, ENSO and vortex state in the terminal |
| `skywatch open` | open the latest dashboard in the default browser |

Global flags: `--json` (machine-readable stdout, quiet stderr — used by launchd),
`--refresh` (ignore cache TTL), `--config PATH`, `-v`.

## Serving over Tailscale

`skywatch serve` makes the app reachable across your tailnet:

```bash
uv run skywatch serve        # binds to this machine's Tailscale IP, port 8092
```

- `http://<tailscale-ip>:8092/` → the latest dashboard
- `/alerts.json`, `/digest.json`, `/briefing.md` → stable URLs for the latest
  run (Hermes can poll these over HTTP instead of reading files)
- `/runs` → browsable history of every past run

`serve.host: tailscale` (the default) auto-detects the machine's 100.x address —
via the Tailscale CLI, falling back to an interface scan — and binds to it
*exclusively*, so nothing is exposed on the LAN. It falls back to 127.0.0.1
with a warning if Tailscale is down. Set an explicit IP or `0.0.0.0` to change
that, and `serve.port` if 8092 clashes with something.

To keep it running permanently, install the second launchd agent (edit the
`/Users/YOURNAME` paths first):

```bash
cp launchd/com.skywatch.serve.plist.example ~/Library/LaunchAgents/com.skywatch.serve.plist
launchctl load ~/Library/LaunchAgents/com.skywatch.serve.plist
```

## Scheduling

No internal scheduler — the app is a one-shot run. Docker users get
`supercronic` in the `scheduler` container ([`docker/crontab`](docker/crontab));
on macOS use launchd:

```bash
cp launchd/com.skywatch.run.plist.example ~/Library/LaunchAgents/com.skywatch.run.plist
# edit paths inside, then:
launchctl load ~/Library/LaunchAgents/com.skywatch.run.plist
```

Default schedule is 06:30 and 16:30 daily; logs land in `logs/`.

## Data sources (all free, all verified with live requests)

| Source | What | Endpoint |
|---|---|---|
| Open-Meteo forecast | ECMWF, GFS, ICON, UKMO daily variables | `api.open-meteo.com/v1/forecast` |
| Open-Meteo ensemble | member-level forecasts (ECMWF 50+ctrl, GFS 30+ctrl) | `ensemble-api.open-meteo.com/v1/ensemble` |
| Open-Meteo archive | ERA5 1991–2020 for per-calendar-date normals | `archive-api.open-meteo.com/v1/archive` |
| NOAA CPC | weekly Niño 3.4 SST + anomaly | `cpc.ncep.noaa.gov/data/indices/wksst9120.for` |
| Open-Meteo 10 hPa | winds used to *compute* the polar vortex index | forecast API, `wind_speed_10hPa` |
| NOAA PSL | NCEP daily climatology of u60N@10hPa via OPeNDAP | `psl.noaa.gov/thredds/...uwnd.day.1981-2010.ltm.nc` |
| Open-Meteo grid | 546-point UK grid, 4 models, for the weather maps | forecast API, multi-location |
| Open-Meteo observed | ERA5 daily observations (~1 day lag) for verification | archive API |
| Blitzortung | real-time lightning strikes (community network) | live websocket feed |

### The vortex index is computed, not fetched

NOAA CPC publishes the 10 hPa zonal-mean zonal wind at 60°N — the sudden-stratospheric-
warming early-warning signal — only as images. Skywatch computes it instead: it samples
the 60°N latitude circle at 12 longitudes from the forecast API, converts each point's
speed/direction to its zonal component (`u = -speed·sin(dir)`), and averages. That gives
a *16-day forecast* of the vortex, validated against the NCEP climatology (computed
late-August value −0.3 m/s vs −1.7 normal; deep-winter normal +35 m/s reproduces).
A reversal (u < 0) in winter often precedes NW-European cold outbreaks by 2–6 weeks —
and is flagged; summer easterlies are normal and explicitly reported as out of season.

## Weather maps

The dashboard draws four map groups from a 0.5° (~35 km) UK/Ireland grid — 546
points × 4 models fetched in a single sub-second call:

- **Where the models disagree** — per-point max-minus-min daily maximum across
  the model panel, drawn for the day with the widest spread this week. The map
  version of Skywatch's core idea.
- **Tomorrow triptych** — panel-median max temperature, precipitation, gusts.
- **7-day temperature strip** — watch the pattern evolve.
- **Regional facts** — Python reduces the grid to sentences ("largest model
  disagreement Thursday: 6.8 °C spread around NE Scotland") which also feed the
  LLM digest, so briefings can talk geography without seeing raw grids.

Maps are inline SVG cells behind a vendored Natural Earth coastline (public
domain, 21 KB) — still zero external assets. Tune `gridmap.*` in config.yaml
(box, step, days, or `enabled: false`). The grid box defaults to the UK and
Ireland; change it for another region (the region-naming table is UK-specific).

## Model accuracy — forecast verification

Skywatch has stored every model's forecast for every target date since its
first run (`state.db`). Once a date has been observed (ERA5, ~1 day behind),
each stored forecast is scored against what actually happened:

- **MAE and bias** per model, per variable, per lead-time bucket (0–2, 3–4,
  5–7, 8+ days ahead). Bias sign tells you a model runs warm/wet/windy.
- **Rank and skill vs the panel median** — the blend is the baseline every
  individual model has to beat ("+41 %" = 41 % lower error than the median).
- **Provisional flags**: ratings are marked provisional until they rest on
  both enough samples (default 20) and enough distinct verified days
  (default 14) — errors within one day are correlated, so sample count alone
  overstates confidence.

Ratings appear on the dashboard ("Model accuracy"), in `skywatch status`, and
in the digest so briefings can weight commentary ("ECMWF, which has verified
best at short range, shows..."). The prompt forbids letting a skill rating
override what the models currently show — it colours commentary, never the
forecast. Tune `skill.*` in config.yaml.

## Storm watch — real-time lightning

`skywatch stormwatch` is a small always-on daemon (its own launchd agent,
`launchd/com.skywatch.stormwatch.plist.example`) listening to the
Blitzortung community lightning network:

- **"Lightning within 5 miles"** — severe, pushed to ntfy as *urgent* the
  moment a strike lands inside `stormwatch.alert_radius_miles`
  (15-minute cooldown so a storm overhead doesn't machine-gun the phone).
- **"Lightning approaching"** — high, when the nearest strike inside 25 miles
  is ≥3 miles closer than in the previous ten-minute window (30-min cooldown).
- **Live strike map**, embedded in the dashboard's "Lightning — live" panel
  (and standalone at `/stormwatch.html`): 5/10/25-mile range rings around
  home, strikes fading with age, auto-refreshing every minute. Alerts fired
  in the last 30 minutes show as a banner above the map.
- **`/stormwatch.json`** for Hermes: strike buffer, nearest distance, alerts.

Detection maths and the alert state machine are pure and unit-tested; verified
end-to-end against a live storm. Strike data © Blitzortung.org and
contributors, free for private, non-commercial use — this is exactly that, but
don't rebroadcast the feed.

## How Hermes (or any agent) consumes alerts

`output/latest/alerts.json` is stable, validated JSON:

```json
{
  "generated_at": "2026-08-21T15:50:59+00:00",
  "generator": "skywatch (llm)",
  "alerts": [
    {
      "severity": "moderate",            // low | moderate | high | severe
      "category": "rain",                // wind|snow|rain|heat|cold|frost_risk|snow_risk|enso|stratosphere
      "title": "Rain: precipitation exceeds threshold Aug 27-28",
      "detail": "…values and which models support them…",
      "confidence": 0.25,                // 0..1, from model agreement
      "valid_from": "2026-08-27",
      "valid_to": "2026-08-28",
      "sources": ["icon_seamless", "gfs_seamless"]
    }
  ]
}
```

`generator` says whether the LLM or the mechanical fallback phrased them — the facts
are identical either way. Poll the file, or run `skywatch status --json` for alerts
plus ENSO/vortex state in one document.

## Adding a data source

1. Create `src/skywatch/sources/<name>.py` with a class exposing `name` and
   `fetch(refresh=False) -> <pydantic payload>`. Use `skywatch.http` for requests
   and `DiskCache` so responses are cached and history accumulates
   (see `openmeteo_forecast.py` for the pattern).
2. Register it in `build_sources()` in `sources/__init__.py`.
3. Reduce it to features in `src/skywatch/features/` (pure functions, unit-tested).
4. Add the feature output to `digest.py` so the LLM can see it.

Please verify a new source against the live API before trusting it — two of the
"obvious" endpoints for this project turned out to be frozen or nonexistent (below).

## Data licences and attribution

| Source | Licence | Obligation |
|---|---|---|
| Open-Meteo (forecasts, ensembles, archive, geocoding) | CC BY 4.0 | attribution — shown in the dashboard footer |
| NOAA CPC / PSL (ENSO, climatology) | US public domain | none |
| Blitzortung.org (lightning) | community data, non-commercial | attribution; do not rebroadcast the feed |
| Natural Earth (coastline) | public domain | none |
| postcodes.io | MIT / OS OpenData (OGL) | none beyond OGL attribution |

Skywatch itself is Apache-2.0 licensed — see [LICENSE](LICENSE).

## Design notes (judgement calls to tune)

- **ENSO file choice**: `wksst8110.for` is frozen at Jan 2021; Skywatch uses
  `wksst9120.for` (1991–2020 base). Niño 3.4 is the *third* column pair — the parser
  asserts the header order and the physical range, and fails loudly on change.
- **Ensemble fetches are one request per model** because the multi-model ensemble
  response uses inconsistent member-key naming.
- **Model horizons differ** (verified: GFS 16d, ECMWF 15d, ICON 7d, UKMO 7d). Every
  disagreement figure carries its panel size; the digest, briefing prompt and
  dashboard all surface panel changes so 2-model agreement is never read as 4-model.
- **Variables**: tmax/tmin, precipitation, wind gusts (mph), snowfall, MSL pressure.
- **Climatology**: 1991–2020 ERA5, ±3-day window around each calendar date. The
  archive snaps to a different grid point than the forecast API (~20 km apart);
  anomalies mix the two, acceptable at Midlands terrain scales.
- **Vortex**: 12-longitude sampling (config `vortex.n_longitudes`); GFS-derived live
  values vs NCEP-R1 climatology is a knowingly mixed baseline, accepted for real-time
  capability. SSW alerts are gated to Nov–Mar (`vortex.season_*`).
- **Thresholds** (config `thresholds.*`): gusts 45/60 mph, snow 2 cm, rain 20 mm/day,
  temp anomaly ±6 °C, frost probability 30 %, snow probability 20 %, spread-jump
  factor 2× (with a 0.5-unit floor so noise near zero never flags), model divergence
  4 °C on tmax. All UK-tuned guesses — tune freely.
- **Trend deltas** compare the cross-model *panel median* per target date across the
  last 6 runs; only shifts ≥1 °C/unit reach the digest.
- **Ensemble probabilities**: frost = members with tmin < 0 °C, snow = members with
  snowfall > 0.5 cm; multiple ensemble systems keep the worst-case probability.
  Negative snowfall member values (a real GFS artifact) are clamped to zero.
- **Reasoning models**: `llm.thinking: false` sends Ollama `think:false`; the token
  budget (`max_tokens: 12288`) still leaves room for models whose template thinks
  anyway. Both LLM calls retry once — an Ollama request that races a 142 GB model
  load can return an empty 200 (observed live).
- **Storm watch**: the Blitzortung feed is a websocket with an LZW-style
  encoding (decoder mirrors their web client); duplicate strike reports are
  deduplicated on (time, position). The approach heuristic compares nearest
  distances between ten-minute windows — simple and testable rather than a
  storm tracker. The monitor buffer (100 mi) is deliberately wider than the
  alert radii so approach trends are visible before they matter.
- **Verification**: forecasts verify against ERA5 at a slightly different grid
  point than the forecast APIs use — absolute MAE is inflated equally for all
  models, so cross-model comparison is fair but absolute numbers flatter no
  one. Both scheduled runs count as samples (they are distinct forecasts).
  Scores recompute each run over a rolling window (default 60 days).
- **Maps**: 0.5° grid includes sea points (drawn honestly; regional facts skip
  them via coarse named region boxes — approximate prose areas, not admin
  boundaries). Each map is a one-hue opacity ramp per variable (orange temp,
  blue precip, aqua gusts, violet disagreement), which adapts to both themes
  automatically. Disagreement day = argmax of the p90 per-point tmax range.
  Grid responses are ~900 KB and history-cached like every source — prune
  `cache/openmeteo_grid/history/` if disk matters.
- **Dashboard** is a static zero-dependency artefact; `skywatch serve` is a thin
  stdlib file server over the output directory, not an app server — the run
  pipeline never depends on it. Default port 8092 (8090/8091 belong to other
  services on the author's machine). Detection quirk, handled: the Tailscale GUI
  app's CLI exits 0 even on failure, and launchd's PATH lacks /sbin, so the
  interface scan uses /sbin/ifconfig by absolute path.
- **Serve-layer performance**: text responses are gzipped (dashboard ~30 KB →
  ~7 KB) with a per-version compression cache; `/` and the latest-run aliases
  are internal rewrites rather than redirects, saving a round trip per visit
  and per Hermes poll; timestamped run paths are served
  `immutable` (they never change) while `latest/` stays `no-cache` with 304
  revalidation. Measured on a throttled 3 Mbps/60 ms link: transfer −76 %,
  first paint 212 ms → 132 ms, zero extra requests or console errors.

## Tests

```bash
uv run pytest          # 69 tests: feature maths, edge cases, parsers, mocked LLM
uv run ruff check src/ tests/
```

The tests pin the traps found during development: the Niño 3.4 column-order regression
(a fixture from the real file asserts +2.7 is read, not Niño 4's +1.0), concatenated
negative anomalies, the panel-shrink-isn't-convergence case, SSW seasonality (August
easterlies never alarm), Feb 29 / year-boundary climatology, missing ensemble members,
and LLM outputs that are invalid, empty, or invent categories.
