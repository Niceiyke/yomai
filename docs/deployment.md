# Deployment Guide

## Docker Compose (recommended)

```bash
# Set your LLM API key
export ANTHROPIC_API_KEY=sk-...

# Start Yomai + Redis
docker compose up -d

# Check health
curl http://localhost:8000/__yomai__/health

# Deep health (includes Redis ping)
curl "http://localhost:8000/__yomai__/health?depth=deep"
```

The compose file starts Yomai on port 8000 with Redis for memory/queue/rate-limiting.

## Production with uvicorn

```bash
# Single worker
yomai serve main:app --host 0.0.0.0 --port 8000

# Multi-worker (recommended for CPU-bound)
YOMAI_WORKERS=4 yomai serve main:app

# Behind nginx / load balancer
yomai serve main:app --proxy-headers
```

## Nginx reverse proxy

SSE streaming requires disabling proxy buffering:

```nginx
server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Required for SSE streaming
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `YOMAI_ENV` | `development` | Set to `production` |
| `YOMAI_LOG_FORMAT` | `json` | `json` or `console` |
| `YOMAI_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `YOMAI_WORKERS` | `1` | uvicorn worker count |
| `YOMAI_API_KEY` | — | Metadata endpoint auth |
| `YOMAI_APP_TITLE` | `Yomai Agent API` | OpenAPI title |

## Health & monitoring

```
GET /__yomai__/health              → {"status": "ok", "version": "0.1.0"}
GET /__yomai__/health?depth=deep   → + {"dependencies": {"llm": ..., "redis": ...}}
GET /__yomai__/metrics             → Prometheus text format (with yomai[metrics])
```
