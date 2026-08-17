# Deployment

Everything on **Render** for now: frontend (static site), backend (Docker
web service), and database (Render's own free Postgres) — one Blueprint
(`render.yaml`), zero external signups. No credit card required.

Two tradeoffs worth knowing up front, both accepted deliberately for a
quick first deploy:
- The free web service spins down after 15 minutes idle and takes 30-60s
  to wake on the next request. For a tool the team dips into occasionally
  rather than continuously, that's a real but bounded cost — no data loss,
  just a slow first load.
- **The free Postgres database gets deleted after 30 days** unless
  upgraded to a paid plan. This is fine to accept while you're trying the
  app out; if it sticks around as something the team actually relies on,
  swap `DATABASE_URL` to a non-expiring free database (e.g. Neon) before
  the 30 days are up — that's a one-line env var change, remove the
  `databases:` block in `render.yaml`, nothing else in the app cares which
  Postgres it's talking to.

## 1. Deploy the Blueprint

1. In the Render dashboard: **New +** → **Blueprint**, connect this GitHub
   repo. Render reads `render.yaml` and shows you three things to create:
   - `sensor-analysis-db` — a free Postgres instance.
   - `sensor-analysis-api` — the FastAPI backend (Docker, `backend/Dockerfile`),
     with `DATABASE_URL` wired to `sensor-analysis-db` automatically.
   - `sensor-analysis-frontend` — the React frontend (static site, built
     from `frontend/`).
2. Click **Apply**. Render creates all three, generates `SESSION_SECRET`
   automatically, and leaves a few env vars blank (marked `sync: false` in
   `render.yaml`) for you to fill in next.

## 2. Run migrations + bootstrap an admin user

Grab the database's connection string from the Render dashboard
(`sensor-analysis-db` → **Connect** → External Connection String), then
from your machine:

```bash
cd backend
DATABASE_URL="<paste the External Connection String>" alembic upgrade head

# There's no self-signup — `users` rows ARE the allowlist — so insert yourself:
psql "<paste the External Connection String>" -c \
  "INSERT INTO users (id, email, name, is_admin) VALUES (gen_random_uuid(), 'you@yourteam.com', 'Your Name', true);"
```

From then on, add teammates the same way (direct SQL, until an admin UI exists).

## 3. Wire the two services together

1. Once `sensor-analysis-api` has deployed, copy its URL (e.g.
   `https://sensor-analysis-api.onrender.com`) and set on it, in its
   **Environment** tab:
   - `GOOGLE_OAUTH_CLIENT_ID` — from the Google Cloud Console OAuth client
     you create for "Sign in with Google" (Authorized JavaScript origins:
     the frontend's Render URL from step 2 below).
   - `ANTHROPIC_API_KEY` — for the AI Insights feature. Leaving it unset
     just disables that one endpoint (503), everything else works fine.
2. Once `sensor-analysis-frontend` has deployed, copy its URL (e.g.
   `https://sensor-analysis-frontend.onrender.com`) and set:
   - On `sensor-analysis-frontend`: `VITE_API_BASE` = the backend URL from
     step 1. **Redeploy the frontend after setting this** — Vite bakes env
     vars in at build time, not read at runtime, so a plain env var change
     alone won't take effect.
   - On `sensor-analysis-api`: `FRONTEND_ORIGIN` = this frontend URL, then
     it redeploys automatically. CORS will reject the frontend's requests
     until this is set correctly.
3. Add the frontend's URL as an Authorized JavaScript origin on the Google
   OAuth client from step 1.

From here, every push to the connected branch redeploys all three
services automatically — that's Render's default behavior, no separate CI
config needed.

## Local development

Backend:
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL for your local Postgres, etc.
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev   # proxies to http://localhost:8000 by default (VITE_API_BASE)
```

Run tests:
```bash
cd backend && pytest
cd frontend && npx vitest run
```
