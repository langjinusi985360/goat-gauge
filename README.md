# GOAT Gauge

GOAT Gauge is a local-first usage dashboard for Command Code GOAT.

## Screenshots

| Dashboard (light) | Dashboard (dark) |
|:---:|:---:|
| ![Dashboard light](docs/screenshots/dashboard-light.png) | ![Dashboard dark](docs/screenshots/dashboard-dark.png) |

| Model usage by cost | Usage records |
|:---:|:---:|
| ![Model usage](docs/screenshots/models-cost.png) | ![Usage records](docs/screenshots/records.png) |

---

It reads the same account and billing endpoints used by the Command Code CLI:

- `GET /alpha/whoami`
- `GET /alpha/billing/credits`
- `GET /alpha/billing/subscriptions`
- `GET /alpha/usage/summary`

The dashboard shows the rolling 5-hour, weekly, and monthly quota, reset
countdowns, credit balances, billing-period statistics, and a locally recorded
usage trend. It runs on `127.0.0.1`, stores history in SQLite, and stores a
saved API key with Windows DPAPI encryption.

It mirrors the GoGauge layout: quota rings, a time-range switch
(today / 7 days / 30 days / all), overview KPIs, a model-usage donut, a daily
trend chart, a paged usage-record table with per-request cost, plus cache hit
rate, cache read/write volume, and per-model hit rate.

## Run

Double-click `start_chrome.bat`, or run:

```powershell
python entry.py --chrome
```

The first run opens a dedicated Chrome profile at the Command Code sign-in
page. Sign in once in that window. GOAT Gauge then captures the authenticated
browser session through Chrome DevTools, keeps the session in memory, and
starts refreshing the dashboard automatically. The dashboard opens in the same
Chrome profile.

If you prefer not to use browser sign-in, the onboarding screen also accepts a
Command Code API key.

To run only the local server:

```powershell
python -m pip install -r requirements.txt
python entry.py --serve --port 18927
```

Then open `http://127.0.0.1:18927`.

Use `--demo` to preview the UI without an API key:

```powershell
python entry.py --chrome --demo
```

## Credential lookup

GOAT Gauge uses either a signed-in Chrome session or an API key. For a browser
session, the key is never handled manually. For API-key mode, it checks:

1. `COMMAND_CODE_API_KEY`
2. `COMMANDCODE_API_KEY`
3. The encrypted key saved by GOAT Gauge
4. Known values in `%USERPROFILE%\.commandcode\auth.json` or
   `%USERPROFILE%\.commandcode\config.json`

If no key or browser session is available, the interface asks for an API key.
It is validated before being saved. The key is never returned to the browser.

## Data

Runtime data defaults to `%LOCALAPPDATA%\GOATGauge`.

Set `GOATGAUGE_DATA` to use a portable data directory.

### Upstream limits

Command Code's usage endpoint only exposes the newest bounded window
(currently 1 day / 100 entries) and ignores range query parameters. GOAT Gauge
therefore keeps every record it has seen in local SQLite, and the
7-day / 30-day / all views are assembled from that local history. Those wider
ranges fill in as the app runs.

The same endpoint returns no session identifier, so per-session cost is not
available and is intentionally omitted rather than guessed.

### Cache metrics

Cache tokens come from `/internal/usage/charts`, which accepts an explicit
`from`/`to` window (roughly one day) and reports `cacheReadInputTokens` /
`cacheCreationInputTokens` per model and time bucket. GOAT Gauge stores those
buckets locally and computes:

- hit rate = cache read tokens / input tokens
- miss tokens = input tokens - cache read tokens

Aggregate KPIs prefer these upstream buckets because they cover the full
upstream window; the record table is a narrower sample (the newest ~100
entries), and the dashboard labels the difference.

GOAT Gauge uses Chrome DevTools port `9333` by default, so it can run
alongside other local tools that use port `9222`. Override it with
`GOATGAUGE_CHROME_CDP_PORT` if needed.

## Tests

```powershell
python -m unittest discover -s tests -v
```

This is an unofficial community project and is not affiliated with Command
Code or its operators.
