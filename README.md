# QuickLaunch

One FastAPI backend, three clients — **web**, **desktop (Windows/Linux)** and **Android** —
built, tested, containerised and published by a single GitHub Actions pipeline.

This repository is a **delivery demo**: the product (a todo list) is deliberately
tiny so that all the attention lands on the pipeline. Nothing here is a mock —
every job runs for real, and the acceptance evidence is the run history.

```
                        ┌──────────────────────────────┐
   git push ──────────▶ │  .github/workflows/pipeline.yml │
                        └───────────────┬──────────────┘
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        │                               │                                │
   ┌────▼─────┐                  ┌──────▼──────┐                  ┌──────▼──────┐
   │ verify-  │                  │  verify-web │                  │  changes    │
   │ backend  │                  │ lint/types/ │                  │ (paths-     │
   │ +postgres│                  │ unit/build  │                  │  filter)    │
   └────┬─────┘                  └──────┬──────┘                  └─────────────┘
        │                               │
        └───────────┬───────────────────┘
                    │
            ┌───────▼────────┐        ┌──────────────────┐   ┌──────────────────┐
            │ docker: build  │        │ desktop: electron│   │ android: gradle  │
            │ + push to GHCR │        │ .exe/.AppImage   │   │ debug .apk       │
            └───────┬────────┘        └────────┬─────────┘   └────────┬─────────┘
                    │                          │                      │
            ┌───────▼────────┐                 │                      │
            │ deploy: compose│                 │                      │
            │ + smoke + health                 │                      │
            │ + rollback test│                 │                      │
            └───────┬────────┘                 │                      │
                    └───────────┬──────────────┴──────────────────────┘
                                │
                        ┌───────▼────────┐
                        │ release: tag + │
                        │ all artifacts  │
                        └────────────────┘
```

## What actually ships

| Target | Artifact | Produced by |
|---|---|---|
| Web + API | `ghcr.io/luty4ng/quicklaunchtemplate:sha-<commit>` (one image, both) | `docker` job |
| Web (static) | `web-<sha>.zip` | `release` job |
| Desktop | `QuickLaunch-Setup-<version>-x64.exe`, `QuickLaunch-<version>-win.zip`, `*.AppImage`, `*.deb` | `desktop` job |
| Desktop updates | `latest.yml` + `*.blockmap` — the feed installed clients read | `desktop` job |
| Android | `app-debug.apk` (installable, debug-signed) | `android` job |

Every default-branch build publishes a release tagged **`v<version>`**, versioned
from the run number (`1.0.<run number>`). The version has to keep going up: the
desktop client only accepts an update to a version **higher** than the one it is
running.

## Desktop auto-update

An installed Windows client checks the release feed on launch, downloads the new
installer in the background and installs it with one click ("Restart and
update"). It reads `latest.yml` from the newest release; electron-builder writes
that file and the `release` job publishes it.

The update path is tested rather than assumed: `desktop-self-test` boots the
packaged app on a Windows runner and has it read a **real published feed** and
report the decision it reaches. To exercise the "update available" branch on
purpose, dispatch with a lower version than what is published:

```
gh workflow run pipeline --ref main -f version=0.0.1 -f draft=true
```

That packages a client older than the published release (as a draft, so nothing
appears on the downloads page), and the self-test must then report
`update-available`.

Installing the update is the one step CI cannot rehearse — it would install and
restart software on the runner — so it is worth clicking once on a real machine.
The build is unsigned, so Windows shows a SmartScreen prompt.

## Why one image for the web

The FastAPI process serves both `/api/*` and the compiled SPA. One artifact, one
port, one health check, and — because the browser only ever talks to its own
origin — **no CORS and no cross-site cookie problem**. The desktop and Android
shells reuse the exact same web bundle, so a UI change is written once.

## Repository layout

```
backend/      FastAPI app, alembic migrations, pytest suite
  app/          config, security (bcrypt+JWT), deps, schemas, routers
  migrations/   versioned schema; `alembic upgrade head` is a pipeline step
  tests/unit/   pure logic, no database
  tests/integration/  real HTTP, real database (sqlite locally, postgres in CI)
web/          Vite + React + TypeScript SPA (the one UI)
desktop/      Electron shell + electron-builder packaging
mobile/       Capacitor config; the android/ project is generated in CI
scripts/      smoke.py (post-deploy gate), gh.py (pipeline driver)
Dockerfile    multi-stage: web build -> python deps -> slim non-root runtime
compose.yaml  db + one-shot migrate service + app
```

## Running it locally

```bash
# API + web, sqlite, no Docker needed
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt
cd web && npm ci && npm run build && cd ..
cd backend && DATABASE_URL=sqlite+aiosqlite:///./data/dev.db \
  JWT_SECRET=local-dev-secret-that-is-long-enough \
  WEB_DIST=../web/dist ../.venv/Scripts/python -m uvicorn app.main:app --port 8000

# then prove it works the same way the pipeline does
python scripts/smoke.py --base-url http://127.0.0.1:8000
```

Full stack with Postgres, as production runs it:

```bash
cp .env.example .env      # then set JWT_SECRET
docker compose up -d --wait
```

## Key design decisions

- **Migrations are their own pipeline step**, never part of the app's start
  command. Concurrent replicas starting up would otherwise race each other.
- **The CD gate is a smoke test, not a health check.** `/api/health` proves the
  database is reachable; `scripts/smoke.py` then registers a user, creates,
  reads, updates and deletes a todo, checks that logout really kills the
  session, and verifies tenant isolation against the live deployment.
- **Tenant isolation is enforced in the query**, not by a post-hoc ownership
  check: every statement carries `user_id = caller`. A foreign id returns 404,
  never 403, so the API does not reveal whether a row exists.
- **Failures stop the line.** No auto-rollback — it hides problems. The previous
  `sha-` tag is the manual rollback anchor, and the pipeline rehearses that
  rollback on every deploy.

## Verification

The pipeline is only worth trusting if it can go red, so three deliberate
regressions were pushed as pull requests and then reverted:

| injected fault | caught by | run |
|---|---|---|
| `(): string => 42` in the web client | `verify-web` → typecheck | [#34652578016](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652578016) |
| an unused import in the backend | `verify-backend` → lint | [#34652632864](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652632864) |
| tenant isolation removed from the todo handlers | `verify-backend` → integration tests | [#34652715044](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34652715044) |

In every case the pull request was blocked and no downstream job (image, desktop,
Android, release) ran.

The last one is the interesting one: 41 of 45 tests still passed. Only the four
cross-tenant cases in `tests/integration/test_isolation.py` noticed, which is why
they live in their own file with their own name.

The fully green run is [#34651815693](https://github.com/luty4ng/QuickLaunchTemplate/actions/runs/34651815693)
(10 jobs, 5.0 min) and its artifacts are on the
[build-12 release](https://github.com/luty4ng/QuickLaunchTemplate/releases/tag/build-12).

## Documentation

- `DESIGN.md` — the original design note for this demo (v1 draft, Next.js-era;
  superseded by the implementation, kept for provenance).
- `report/REPORT.md` — the delivery report: what was built, how the pipeline
  works, measured timings, and the evidence from real runs.
