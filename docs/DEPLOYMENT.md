# Deployment

## Topology

| Component | Recommended host | Artifact |
|---|---|---|
| Web | Vercel (Root Directory `apps/web`, framework Next.js) or any Node 22 host | `apps/web/Dockerfile` (standalone output) |
| API | Managed containers (Fly.io, Railway, Cloud Run, ECS) | `apps/api/Dockerfile` — runs `alembic upgrade head` then uvicorn |
| Worker | Same image, command `arq app.workers.tasks.WorkerSettings` | 1+ replicas (cron jobs are idempotent) |
| PostgreSQL 16 | Neon, Supabase, RDS, Cloud SQL | `DATABASE_URL=postgresql+asyncpg://…` |
| Redis 7 | Upstash, ElastiCache, Memorystore | `REDIS_URL` |
| Errors | Sentry | `SENTRY_DSN` |
| Traces | Any OTLP/HTTP collector (Honeycomb, Grafana, Datadog) | `OTEL_EXPORTER_OTLP_ENDPOINT` |

## Vercel (web)

1. Import the repo, set **Root Directory** to `apps/web` (pnpm workspace is detected; `apps/web/vercel.json` pins commands).
2. Environment: all `NEXT_PUBLIC_*` variables from `apps/web/.env.example`.
3. `NEXT_PUBLIC_API_URL` must be the API origin; add the web origin to the API's `CORS_ORIGINS`, and set
   `APP_DOMAIN`/`APP_ORIGIN` to the web domain (SIWE messages are bound to it).

## API / worker containers

```bash
docker build -f apps/api/Dockerfile -t agent98-api .
docker run -p 8000:8000 --env-file apps/api/.env agent98-api
docker run --env-file apps/api/.env agent98-api arq app.workers.tasks.WorkerSettings
```

Health: `GET /v1/health` (liveness), `GET /v1/health/ready` (DB + Redis; 503 if DB down).
The `deploy.yml` workflow builds and pushes the API image to GHCR; wire the final rollout step to your platform.

## Local stack

`docker compose up --build` starts postgres, redis, api, worker and web. (The Dockerfiles and Compose file were
written for this repo but could not be executed in the build sandbox — no Docker daemon — and are exercised by the
`docker` job in CI.)
