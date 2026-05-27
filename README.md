# WeatherBot — Self-Healing Microservices Platform

A thesis research project exploring **autonomous fault detection and recovery** in distributed systems. WeatherBot is a conversational weather assistant built as seven containerised microservices, instrumented with ML-driven anomaly detection, circuit breakers, adaptive rate limiting, and automated self-healing — all observable through a real-time React dashboard.

---

## Architecture

```
User (browser)
      │  HTTP POST /weather_form
      ▼
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│   frontend  │────▶│ nlu_service │     │ responder_service │
│  (port 8000)│     │ (port 8005) │     │   (port 8007)     │
│  API Gateway│◀────│ City/date   │     │  LLM via          │
│  + CB client│     │ extraction  │     │  OpenRouter       │
└──────┬──────┘     └─────────────┘     └──────────────────┘
       │                                        ▲
       │  GET /weather?city=...                 │
       ▼                                        │
┌─────────────┐                                 │
│weather_serv │─────────────────────────────────┘
│ (port 8001) │   weather facts passed to LLM
│ OpenWeather │
│    API      │
└─────────────┘

       All services log to ──▶ ┌──────────────────┐     ┌─────────┐
                                │ logging_service  │────▶│ log_db  │
                                │  (port 8004)     │     │Postgres │
                                │ Centralised logs │     │(port    │
                                │ + heal endpoint  │     │ 8009)   │
                                └──────────────────┘     └─────────┘

                                         ▲  reads logs
                                         │
                               ┌─────────────────────┐
                               │ anomaly_detection   │
                               │   (port 8002)       │
                               │ Isolation Forest ML │
                               │ Circuit Breakers    │
                               │ Rate Limiters       │
                               │ Self-healing loop   │
                               └─────────────────────┘
                                         │ serves metrics
                                         ▼
                               ┌─────────────────────┐
                               │     dashboard       │
                               │   (port 3000)       │
                               │ React + Recharts    │
                               │ Real-time polling   │
                               └─────────────────────┘
```

---

## Key Technical Contributions

### ML-Based Anomaly Detection (Isolation Forest)
Each microservice is independently monitored by a continuously-retrained Isolation Forest model. Feature engineering extracts five signals from raw log data: `latency_ms`, `is_error`, `response_size`, `hour_of_day`, `day_of_week`. Models retrain hourly on a 60-minute rolling window and detect anomalies in the last 5 minutes (contamination = 5%, score threshold = −0.15).

### Circuit Breaker Pattern (3-State FSM)
A per-service circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED) prevents cascading failures. Frontend reports every downstream call outcome; the breaker opens after 3 consecutive failures, attempts recovery after 30 seconds, and closes after 2 successes in HALF_OPEN state.

### Adaptive Rate Limiting (Token Bucket)
Each service has an independent token-bucket rate limiter (10 req/s normal, burst 20). On detection of a `high_latency` anomaly the system automatically throttles the affected service to 30% capacity and restores it on recovery.

### Automated Self-Healing
The anomaly detection service runs a background loop every 10 seconds. It classifies detected anomalies by type and severity, then dispatches healing actions:

| Anomaly Type | Severity | Action |
|---|---|---|
| `service_error` | high | Restart container via Docker API |
| `service_error` | medium | Monitor (log only) |
| `high_latency` | any | Rate-limit to 30% |
| `statistical_anomaly` | any | Monitor (log only) |

### Explainable AI (XAI)
Every anomaly record includes a structured explanation: isolation score, per-feature values vs. baseline statistics, and a human-readable detection reason. The dashboard renders these explanations alongside the anomaly feed.

### Centralised Structured Logging
All seven services send structured JSON log events to `logging_service`, which stores them in PostgreSQL (`service_logs` table). The logging service also acts as the healing command receiver — the anomaly detector POSTs healing intents here, which then executes Docker restarts via the mounted socket.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | Python 3.10 + FastAPI + Uvicorn |
| ML / anomaly detection | scikit-learn (Isolation Forest), pandas, NumPy |
| Database | PostgreSQL 15 (via SQLAlchemy + asyncpg) |
| LLM integration | OpenRouter API (Grok model) |
| Weather data | OpenWeatherMap REST API |
| NLU | Regex-based city + date extraction |
| Containerisation | Docker + Docker Compose |
| Container orchestration | Docker SDK (Python) for programmatic restarts |
| Frontend | React 18 + Vite + Recharts |
| Dashboard serving | Nginx (multi-stage Docker build) |

---

## Services

| Service | Port | Purpose |
|---|---|---|
| `frontend` | 8000 | API gateway, orchestrates NLU → Weather → LLM pipeline, circuit breaker client |
| `weather_service` | 8001 | OpenWeatherMap wrapper, structured error handling |
| `anomaly_detection_service` | 8002 | Isolation Forest models, circuit breakers, rate limiters, self-healing loop |
| `logging_service` | 8004 | Centralised log store (PostgreSQL), healing executor |
| `nlu_service` | 8005 | Extracts city and date from free-text queries |
| `responder_service` | 8007 | LLM-generated natural language responses |
| `log_db` | 8009 | PostgreSQL 15 database |
| `dashboard` | 3000 | Real-time React monitoring UI |

---

## Getting Started

### Prerequisites
- Docker Desktop (or Docker Engine + Compose plugin)
- API keys for OpenWeatherMap and OpenRouter (see below)

### Setup

```bash
git clone https://github.com/<your-username>/weatherBot.git
cd weatherBot/weatherbot

# Copy the environment template and fill in your keys
cp .env.example .env
# Edit .env with your actual keys
```

Required values in `.env`:

```
API_KEY=<OpenWeatherMap key>
OPENROUTER_API_KEY=<OpenRouter key>
POSTGRES_PASSWORD=<choose any strong password>
```

### Run

```bash
docker-compose up --build
```

| Service | URL |
|---|---|
| Weather chatbot UI | http://localhost:8000 |
| Monitoring dashboard | http://localhost:3000 |
| Anomaly detection API | http://localhost:8002/docs |
| Logging API | http://localhost:8004/docs |

### Stop

```bash
docker-compose down
```

To also remove the database volume:

```bash
docker-compose down -v
```

---

## Key API Endpoints

### frontend (8000)
| Method | Path | Description |
|---|---|---|
| GET | `/` | Chat UI (HTML form) |
| POST | `/weather_form` | Submit free-text weather query |
| GET | `/health` | Dependency health check |

### anomaly_detection_service (8002)
| Method | Path | Description |
|---|---|---|
| GET | `/anomalies/recent` | Recent detected anomalies |
| GET | `/healing/history` | Executed healing actions |
| GET | `/circuit-breakers` | Live circuit breaker states |
| GET | `/stats` | Aggregated anomaly statistics |
| POST | `/detect/manual` | Trigger immediate detection cycle |
| POST | `/circuit-breakers/{service}/reset` | Manual circuit breaker reset |

### logging_service (8004)
| Method | Path | Description |
|---|---|---|
| POST | `/log` | Ingest a log event |
| GET | `/logs` | Query logs with filters |
| POST | `/heal` | Receive and execute a healing action |

---

## Experiment Results

The system was evaluated using a controlled fault injection framework (`run_clean_experiment_v4.py`) across:

- **12 independent trials**
- **6 fault types**: `high_latency`, `service_error`, `error_burst`, `mixed`, `slow_degradation`, `memory_pressure`
- **3 target services**: `weather_service`, `nlu_service`, `responder_service`
- **216 total test combinations**

Each trial injected 50 synthetic fault log entries and measured precision, recall, F1-score, and detection latency. Raw results are in [`weatherbot/clean_results_v4.csv`](weatherbot/clean_results_v4.csv).

---

## Project Context

This system was built as part of a thesis on **autonomous fault management in microservice architectures**. The core research question: can ML-based anomaly detection combined with programmatic healing actions meaningfully reduce mean time to recovery (MTTR) in a real microservice system without human intervention?

The architecture deliberately mirrors production patterns (circuit breakers, rate limiting, centralised observability) to make the findings applicable beyond the research context.

---

## License

MIT
