# WeatherBot — Services Directory

This directory contains all eight services that make up the WeatherBot platform.

See the [root README](../README.md) for full architecture documentation, setup instructions, and experiment results.

## Quick Reference

| Service | Port | Entry point |
|---|---|---|
| `frontend/` | 8000 | `app.py` — API gateway + circuit breaker client |
| `weather_service/` | 8001 | `main.py` — OpenWeatherMap wrapper |
| `anomaly_detection_service/` | 8002 | `app.py` — Isolation Forest + self-healing loop |
| `logging_service/` | 8004 | `app.py` — Centralised log store (PostgreSQL) |
| `nlu_service/` | 8005 | `app.py` — City/date extraction |
| `responder_service/` | 8007 | `app.py` — LLM response generation (OpenRouter) |
| `log_db` | 8009 | PostgreSQL 15 (Docker image, no local source) |
| `dashboard/` | 3000 | `src/App.jsx` — React monitoring UI |

## Running

```bash
cp .env.example .env   # fill in your API keys
docker-compose up --build
```
