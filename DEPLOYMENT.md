# Deployment

Two-vendor stack (see the migration plan for the reasoning): **Vercel** for
the React frontend, **Render** for the FastAPI backend + managed Postgres.

## Backend + database (Render)

1. In the Render dashboard, "New +" → "Blueprint", point it at this repo.
   Render reads `render.yaml` at the repo root and provisions:
   - `sensor-analysis-db` — a managed Postgres 16 instance.
   - `sensor-analysis-api` — the FastAPI app, built from `backend/Dockerfile`.
2. `DATABASE_URL` and `SESSION_SECRET` are wired automatically by the
   blueprint. Set these two manually in the Render dashboard's environment
   tab (not committed, per `backend/.env.example`):
   - `GOOGLE_OAUTH_CLIENT_ID` — from the Google Cloud Console OAuth client
     you create for "Sign in with Google" (Authorized JavaScript origins:
     your Vercel URL; no redirect URI needed for the token-based flow used
     here).
   - `ANTHROPIC_API_KEY` — for the AI Insights feature. Omitting it just
     disables that one endpoint (503), the rest of the app works fine.
3. After the first deploy, run the initial admin bootstrap once (see
   below) so at least one person can sign in — remember, there's no
   self-signup, `users` rows are the allowlist.
4. Update `FRONTEND_ORIGIN` (in render.yaml or the dashboard) to the actual
   Vercel URL once step 2 below has one, then redeploy — CORS will reject
   the frontend's requests otherwise.

### Bootstrapping the first admin user

Migrations create the schema but no rows. Connect to the Render Postgres
(the dashboard gives you a `psql` connection string) and insert yourself:

```sql
INSERT INTO users (id, email, name, is_admin)
VALUES (gen_random_uuid(), 'you@yourteam.com', 'Your Name', true);
```

From then on, use the app itself (or direct SQL, until an admin UI exists)
to add teammates the same way.

## Frontend (Vercel)

1. Import the repo into Vercel. In the project's Settings → General, set
   **Root Directory** to `frontend/` (this is a monorepo — Vercel needs to
   know which subdirectory to build; `frontend/vercel.json` only covers the
   build command/output/rewrites, not the root directory).
2. Add an environment variable `VITE_API_BASE` = your Render backend's URL
   (e.g. `https://sensor-analysis-api.onrender.com`).
3. Deploy. Vercel auto-detects Vite and uses `frontend/vercel.json`'s
   `buildCommand`/`outputDirectory`; the `rewrites` entry makes client-side
   routing (React Router) work on a hard refresh/deep link.

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
