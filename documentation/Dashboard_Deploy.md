# Market Tracker deploy (btc.rickarko.com)

Operator runbook for publishing the local `ccquant dashboard` HTML to
`https://btc.rickarko.com`. Generation stays on the research machine;
Fly only serves static files.

See also [`Machine_Setup.md`](Machine_Setup.md) for DuckDB bootstrap.

---

## 1. Architecture

The Market Tracker is a **self-contained HTML file**. `ccquant dashboard`
renders it from local DuckDB. The live BTC tape refreshes **in the
visitor's browser** against public Binance (then Coinbase) APIs — there is
no server-side price proxy.

Fly runs `nginx:1.27-alpine` from [`deploy/Dockerfile`](../deploy/Dockerfile)
with [`deploy/nginx.conf`](../deploy/nginx.conf). The image copies
`deploy/public/index.html` at **build** time. [`.dockerignore`](../.dockerignore)
sends only `deploy/` to the builder (not `.venv`, `data/`, or `.env`).
There is no DuckDB volume, no Python runtime, and **no API keys on Fly**.

```
research machine:  sync → ccquant dashboard → dashboard.check → fly deploy
visitor browser:   https://btc.rickarko.com  +  Binance/Coinbase (live tape)
```

---

## 2. Cost

| Knob | Value |
|---|---|
| VM | Fly `shared-cpu-1x`, 256MB RAM |
| Autostop | `min_machines_running = 0`, `auto_stop_machines = "stop"` |
| Idle | ≈ $0 |
| Light traffic | a few dollars/month |
| Cold start | 2–5s after idle — expected, not an outage |

Scale up (`min_machines_running = 1`) only if cold start becomes annoying.

---

## 3. One-time setup

Needs a Fly account (`fly auth login`) and Route 53 access for
`rickarko.com`. Do **not** create a wildcard `*.rickarko.com` record
pointing at Fly.

```bash
fly auth login
make fly.app
make fly.certs
# Then add the exact records fly prints for btc.rickarko.com in Route 53.
# Specific btc record only — never *.rickarko.com → Fly.
fly certs show btc.rickarko.com -a ccquant-btc
```

Wait until `fly certs show` reports the certificate as issued. Records Fly
printed for this app (confirm with `fly ips list -a ccquant-btc` before
pasting — addresses can change):

```
A     btc.rickarko.com → 66.241.125.184
AAAA  btc.rickarko.com → 2a09:8280:1::17b:6ec3:0
```

Do **not** create `*.rickarko.com`. Verify:

```bash
dig +short btc.rickarko.com
curl -sI https://btc.rickarko.com/healthz
```

Do not call `aws route53 change-resource-record-sets` from this repo;
paste the Fly-printed records into Route 53 by hand.

---

## 4. Routine publish

```bash
uv run ccquant sync all          # or a cheaper tail; operator choice
make dashboard.deploy            # stage + check + fly deploy
make fly.smoke
```

`make fly.deploy` is the same path (`dashboard.stage` → `dashboard.check`
→ `fly deploy`). There is no skip flag for the checker.

Staged HTML is gitignored (`deploy/public/*.html`). Never commit DuckDB,
`.env`, or the rendered snapshot.

---

## 5. Local preview

```bash
make dashboard.serve
# then http://127.0.0.1:8080/  and  http://127.0.0.1:8080/healthz
```

Requires Docker. If Docker is missing, the target exits with a pointer at
`python3 -m http.server --directory deploy/public 8080` (docs-only
fallback; no security headers).

---

## 6. Rollback

```bash
fly releases --app ccquant-btc --image
fly deploy --app ccquant-btc --image registry.fly.io/ccquant-btc:<previous>
```

`fly releases --image` prints the Docker image reference for each release.
Redeploy the previous image to roll back. This flyctl has no
`releases rollback` subcommand. Do not `fly deploy --force` unless a
deploy is stuck and you know why.

---

## 7. Failure modes

| Symptom | Cause | What to do |
|---|---|---|
| `ccquant dashboard` errors | Empty or stale DuckDB | `uv run ccquant status`; restore via [`Machine_Setup.md`](Machine_Setup.md) / `sync bootstrap` |
| Live tape blank, rest of page OK | Geo-blocked Binance | Expected fallback to Coinbase in the page JS; not a Fly outage |
| First request slow | Autostop cold start (2–5s) | Not a bug. Keep a machine running only if it bothers you |
| Charts blank, chrome loads | Plotly CDN outage | Page still useful; vendoring Plotly is a later change |
| `dashboard.check` fails | Secret-like substring or stub HTML | Do **not** deploy. Inspect `deploy/public/index.html` |
| Docker build `test -s index.html` fails | Forgot `dashboard.stage` | `make dashboard.stage` then deploy again |

---

## 8. What is not on Fly

Leave these on the research machine:

- `.env` (`FRED_API_KEY`, `CG_DEMO_API_KEY`, `BITCOIN_IS_DATA_KEY`, …)
- DuckDB (`data/ccquant.duckdb`)
- Wallet extracts / RPC
- dbt models (already baked into the HTML snapshot at render time)

The Fly image is nginx + one HTML file.

---

## 9. DNS coexistence

| Name | Where |
|---|---|
| `rickarko.com` (apex) | Portfolio / AWS App Runner |
| `{company}.rickarko.com` | RickArkoOS career sites (CloudFront + S3) |
| `btc.rickarko.com` | **This app** — specific record → Fly (`ccquant-btc`) |

Specific records coexist. A wildcard `*.rickarko.com` → Fly (or App
Runner) would fight the career aliases. Do not create one.

Makefile surface: `make help` (targets `dashboard.*` / `fly.*`).
