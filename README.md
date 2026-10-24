# Movesense Trajectory App

Next.js frontend + FastAPI backend for Movesense IMU capture and A→B trajectory
analysis.

This repository deploys as **one Vercel project** using
[Vercel Services](https://vercel.com/docs/services): the Next.js app serves the
site, the FastAPI app serves `/api/*`, both on the same domain.

## Repository layout

```text
.
├── client/          # Next.js 16 frontend (service: frontend)
├── server/          # FastAPI + BLE/analysis backend (service: backend)
├── vercel.json      # Declares both services and the public routes
└── package.json     # Convenience scripts (dev/build/lint)
```

`vercel.json` routes requests before they reach either app:

| Request        | Service    |
| -------------- | ---------- |
| `/api/*`       | `backend`  |
| everything else | `frontend` |

Because both run on the same origin, the browser calls relative `/api/...`
URLs. No CORS setup and no `NEXT_PUBLIC_API_URL` are needed in production.

## Deploy to Vercel (one project)

1. Push this repository to GitHub.
2. In Vercel, choose **Add New → Project** and import the repository.
3. Keep **Root Directory** at the repository root (not `client/` or `server/`).
4. Vercel reads `vercel.json` and detects the **Services** preset with both
   services. If the preset is still `Next.js`, change the project's framework to
   **Services** (Project → Settings → General → Framework Preset).
5. Deploy. No environment variables are required.

CLI alternative:

```bash
npm i -g vercel
vercel link          # link/create the project
vercel deploy        # preview
vercel deploy --prod # production
```

Verify the deployment:

```bash
curl https://<your-deployment>/api/health
```

The response includes `bluetooth_available`, `serverless`, and
`writable_data_dir` so you can confirm the runtime configuration.

## Local development

### One command (recommended, matches production routing)

```bash
npm i -g vercel
vercel dev --local
```

`vercel dev --local` starts both services locally and rewrites `/api/*` to FastAPI.

### Separate dev servers

```bash
# terminal 1 – backend
cd server
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements-dev.txt
uvicorn api:app --reload --port 8000

# terminal 2 – frontend
cd client
npm install
set NEXT_PUBLIC_API_URL=http://localhost:8000   # PowerShell: $env:NEXT_PUBLIC_API_URL="http://localhost:8000"
npm run dev
```

Without `vercel dev`, set `NEXT_PUBLIC_API_URL` because the Next.js dev server
does not proxy `/api` to FastAPI.

From the repository root you can also run:

```bash
npm run dev:all      # vercel dev --local (both services)
npm run build        # builds the Next.js client
npm run dev:client   # Next.js only (set NEXT_PUBLIC_API_URL)
npm run dev:server   # uvicorn api:app from server/ (needs Python deps)
```

## Runtime behaviour on Vercel

The backend is designed to boot without a Bluetooth adapter and without a
persistent disk:

- **Storage** – Vercel mounts the deployment read-only. The API detects the
  serverless runtime and writes raw captures / summaries /
  `users.json` under `/tmp/movesense` instead of `server/data`. Bundled sample
  trajectories are copied into that writable directory on cold start. You can
  override the location with `MOVESENSE_DATA_DIR`.
- **Bluetooth** – Vercel Functions have no Bluetooth adapter. `/api/health`
  reports `bluetooth_available: false` and device scan/connect endpoints return
  a clear `503` with a Bluetooth error message. Every non-BLE feature (accounts,
  sample reports, exports) still works.
- **Sessions** – `/tmp` is per function instance and ephemeral. Accounts and
  captures created on one instance are not shared with other instances and are
  lost on redeploy/cold start.
- **Live tracking** – streaming runs as an in-process background task, which
  Vercel freezes once the response is returned. Real-time capture needs a
  long-running process.

**For real sensor work**, run `server/` on a machine with Bluetooth (or a
stateful host) and point the frontend at it with `NEXT_PUBLIC_API_URL`. The
Vercel deployment remains a single project that can host the UI and a demo API.

## Optional environment variables

| Variable                     | Where   | Purpose                                                        |
| ---------------------------- | ------- | -------------------------------------------------------------- |
| `NEXT_PUBLIC_API_URL`        | client  | Point the browser at an external API; leave unset on Vercel.   |
| `MOVESENSE_DATA_DIR`         | server  | Writable directory for captures/state (default `/tmp/movesense` on Vercel). |
| `MOVESENSE_SECURE_COOKIES`   | server  | `1` forces `Secure` session cookies (automatic on Vercel).      |
| `MOVESENSE_ALLOWED_ORIGINS`  | server  | Comma-separated extra CORS origins.                             |

## Tests

```bash
cd server
pip install -r requirements-dev.txt
pytest
```
