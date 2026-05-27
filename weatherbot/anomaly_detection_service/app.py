# anomaly_detection_service/app.py
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import asyncio
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import pandas as pd
from typing import List, Dict, Optional, Any
import json
import requests
from pydantic import BaseModel
import logging
import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("anomaly_detector")


# ------------------------------------------------------------------ #
# Configuration
# ------------------------------------------------------------------ #

class Config:
    DATABASE_URL = f"postgresql://postgres:{os.getenv('POSTGRES_PASSWORD', 'password')}@log_db/logdb"
    CHECK_INTERVAL_SECONDS = 10
    ANALYSIS_WINDOW_MINUTES = 60
    MIN_TRAINING_SAMPLES = 30
    DETECTION_WINDOW_MINUTES = 5
    ANOMALY_SCORE_THRESHOLD = -0.15
    CONTAMINATION = 0.05

config = Config()


# ------------------------------------------------------------------ #
# Circuit Breaker
# ------------------------------------------------------------------ #

class CircuitBreaker:
    """
    Three-state circuit breaker per service.
    CLOSED  = normal operation, requests flow through
    OPEN    = service is failing, requests blocked, fallback returned
    HALF    = testing if service has recovered (one request allowed through)

    Thesis reference: Chapter 1.3.3 — Healing Actions and Strategies,
    hierarchical recovery (soft → medium → hard interventions).
    """
    CLOSED   = "closed"
    OPEN     = "open"
    HALF     = "half_open"

    def __init__(self,
                 failure_threshold: int = 3,
                 recovery_timeout_s: int = 30,
                 success_threshold: int = 2):
        self.failure_threshold  = failure_threshold   # failures before opening
        self.recovery_timeout_s = recovery_timeout_s  # seconds before trying HALF
        self.success_threshold  = success_threshold   # successes to close again

        # Per-service state
        self._state:           Dict[str, str]   = {}
        self._failure_count:   Dict[str, int]   = {}
        self._success_count:   Dict[str, int]   = {}
        self._opened_at:       Dict[str, float] = {}
        self._last_action:     Dict[str, str]   = {}

    def _init_service(self, service: str):
        if service not in self._state:
            self._state[service]         = self.CLOSED
            self._failure_count[service] = 0
            self._success_count[service] = 0
            self._opened_at[service]     = 0.0
            self._last_action[service]   = "none"

    def get_state(self, service: str) -> str:
        self._init_service(service)
        # Auto-transition OPEN → HALF after recovery timeout
        if (self._state[service] == self.OPEN and
                time.time() - self._opened_at[service] > self.recovery_timeout_s):
            self._state[service] = self.HALF
            logger.info(f"[CircuitBreaker] {service}: OPEN → HALF_OPEN")
        return self._state[service]

    def record_failure(self, service: str) -> dict:
        self._init_service(service)
        self._failure_count[service] += 1
        self._success_count[service] = 0

        if (self._state[service] in (self.CLOSED, self.HALF) and
                self._failure_count[service] >= self.failure_threshold):
            self._state[service]       = self.OPEN
            self._opened_at[service]   = time.time()
            self._failure_count[service] = 0
            action = "circuit_opened"
            logger.warning(f"[CircuitBreaker] {service}: → OPEN after {self.failure_threshold} failures")
        else:
            action = "failure_recorded"

        self._last_action[service] = action
        return {"action": action, "state": self._state[service],
                "failure_count": self._failure_count[service]}

    def record_success(self, service: str) -> dict:
        self._init_service(service)
        if self._state[service] == self.HALF:
            self._success_count[service] += 1
            if self._success_count[service] >= self.success_threshold:
                self._state[service]         = self.CLOSED
                self._failure_count[service] = 0
                self._success_count[service] = 0
                action = "circuit_closed"
                logger.info(f"[CircuitBreaker] {service}: HALF_OPEN → CLOSED")
            else:
                action = "success_recorded"
        else:
            self._failure_count[service] = max(0, self._failure_count[service] - 1)
            action = "success_recorded"

        self._last_action[service] = action
        return {"action": action, "state": self._state[service]}

    def status(self) -> dict:
        return {
            svc: {
                "state":         self._state[svc],
                "failure_count": self._failure_count[svc],
                "last_action":   self._last_action[svc],
            }
            for svc in self._state
        }


# ------------------------------------------------------------------ #
# Rate Limiter (Token Bucket)
# ------------------------------------------------------------------ #

class RateLimiter:
    """
    Token bucket rate limiter per service.
    Under normal load: bucket is full, all requests pass.
    Under high latency: bucket refill rate is reduced (throttling).
    When latency returns to normal: refill rate is restored.

    Thesis reference: Chapter 1.3.3 — adaptive resource management
    as a response to high-latency anomalies.
    """

    def __init__(self, default_rate: float = 10.0, burst: int = 20):
        self.default_rate = default_rate   # tokens per second (normal)
        self.burst        = burst          # max bucket size

        self._tokens:       Dict[str, float] = {}
        self._last_refill:  Dict[str, float] = {}
        self._current_rate: Dict[str, float] = {}
        self._throttled:    Dict[str, bool]  = {}

    def _init_service(self, service: str):
        if service not in self._tokens:
            self._tokens[service]       = float(self.burst)
            self._last_refill[service]  = time.time()
            self._current_rate[service] = self.default_rate
            self._throttled[service]    = False

    def _refill(self, service: str):
        now     = time.time()
        elapsed = now - self._last_refill[service]
        self._tokens[service] = min(
            self.burst,
            self._tokens[service] + elapsed * self._current_rate[service]
        )
        self._last_refill[service] = now

    def throttle(self, service: str, factor: float = 0.3) -> dict:
        """
        Reduce refill rate to `factor` of normal.
        Called when high_latency anomaly is detected.
        """
        self._init_service(service)
        new_rate = self.default_rate * factor
        self._current_rate[service] = new_rate
        self._throttled[service]    = True
        logger.warning(f"[RateLimiter] {service}: throttled to {new_rate:.1f} req/s "
                       f"({factor*100:.0f}% of normal)")
        return {
            "action":       "rate_limited",
            "service":      service,
            "new_rate":     new_rate,
            "normal_rate":  self.default_rate,
            "reduction_pct": int((1 - factor) * 100),
        }

    def restore(self, service: str) -> dict:
        """Restore normal rate. Called when service recovers."""
        self._init_service(service)
        self._current_rate[service] = self.default_rate
        self._throttled[service]    = False
        logger.info(f"[RateLimiter] {service}: rate restored to {self.default_rate:.1f} req/s")
        return {"action": "rate_restored", "service": service,
                "rate": self.default_rate}

    def allow_request(self, service: str) -> bool:
        """Returns True if a request should be allowed through."""
        self._init_service(service)
        self._refill(service)
        if self._tokens[service] >= 1.0:
            self._tokens[service] -= 1.0
            return True
        return False

    def status(self) -> dict:
        return {
            svc: {
                "throttled":    self._throttled[svc],
                "current_rate": round(self._current_rate[svc], 2),
                "normal_rate":  self.default_rate,
                "tokens":       round(self._tokens[svc], 2),
            }
            for svc in self._tokens
        }


# ------------------------------------------------------------------ #
# Pydantic models
# ------------------------------------------------------------------ #

class AnomalyRecord(BaseModel):
    id: Optional[int] = None
    timestamp: datetime
    service: str
    anomaly_type: str
    score: float
    features: Dict[str, Any]
    log_id: Optional[int] = None
    description: str
    severity: str
    healed: bool = False
    explanation: Optional[Dict[str, Any]] = None


# ------------------------------------------------------------------ #
# DB helpers
# ------------------------------------------------------------------ #

def get_db_connection():
    return psycopg2.connect(dsn=config.DATABASE_URL, cursor_factory=RealDictCursor)


def row_to_dict(row) -> dict:
    d = dict(row)
    ts = d.get("timestamp")
    if ts is not None and not isinstance(ts, datetime):
        d["timestamp"] = datetime.fromisoformat(str(ts))
    return d


def create_anomaly_tables():
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detected_anomalies (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL,
            service VARCHAR(100) NOT NULL,
            anomaly_type VARCHAR(50) NOT NULL,
            score FLOAT NOT NULL,
            features JSONB,
            log_id INTEGER,
            description TEXT,
            severity VARCHAR(20) NOT NULL,
            healed BOOLEAN DEFAULT FALSE,
            explanation JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS healing_actions (
            id SERIAL PRIMARY KEY,
            anomaly_id INTEGER REFERENCES detected_anomalies(id),
            service VARCHAR(100) NOT NULL,
            action VARCHAR(100) NOT NULL,
            reason TEXT,
            success BOOLEAN,
            executed_at TIMESTAMP DEFAULT NOW(),
            details JSONB
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_service   ON detected_anomalies(service)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_timestamp ON detected_anomalies(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_severity  ON detected_anomalies(severity)")
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("Anomaly tables created/verified")


def store_anomaly(anomaly: AnomalyRecord, cursor) -> Optional[int]:
    """Store anomaly only if this log_id has not been flagged before."""
    if anomaly.log_id is not None:
        cursor.execute("""
            SELECT id FROM detected_anomalies
            WHERE log_id = %s LIMIT 1
        """, (anomaly.log_id,))
        if cursor.fetchone():
            return None  # Already stored, skip silently

    cursor.execute("""
        INSERT INTO detected_anomalies
        (timestamp, service, anomaly_type, score, features, log_id,
         description, severity, healed, explanation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        anomaly.timestamp, anomaly.service, anomaly.anomaly_type,
        anomaly.score, json.dumps(anomaly.features), anomaly.log_id,
        anomaly.description, anomaly.severity, anomaly.healed,
        json.dumps(anomaly.explanation) if anomaly.explanation else None,
    ))
    anomaly_id = cursor.fetchone()["id"]
    logger.info(f"Stored anomaly id={anomaly_id} service={anomaly.service} severity={anomaly.severity}")
    return anomaly_id


# ------------------------------------------------------------------ #
# Feature engineering
# ------------------------------------------------------------------ #

def extract_features(logs: List[dict]) -> pd.DataFrame:
    rows = []
    for log in logs:
        ts = log.get("timestamp")
        if not isinstance(ts, datetime):
            ts = datetime.fromisoformat(str(ts))
        rows.append({
            "latency_ms":    float(log.get("latency_ms") or 0),
            "is_error":      1.0 if log.get("status") == "error" else 0.0,
            "response_size": float(len(str(log.get("response") or ""))),
            "hour_of_day":   float(ts.hour),
            "day_of_week":   float(ts.weekday()),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ #
# Anomaly Detection Engine
# ------------------------------------------------------------------ #

class AnomalyDetectionEngine:
    def __init__(self):
        self.models:         Dict[str, IsolationForest] = {}
        self.scalers:        Dict[str, StandardScaler]  = {}
        self.baseline_stats: Dict[str, dict]            = {}
        self.last_trained:   Dict[str, datetime]        = {}

    def train(self, service: str, training_logs: List[dict]) -> bool:
        if len(training_logs) < config.MIN_TRAINING_SAMPLES:
            logger.warning(f"[{service}] Only {len(training_logs)} samples — need {config.MIN_TRAINING_SAMPLES}")
            return False

        df = extract_features(training_logs)
        scaler = StandardScaler()
        X = scaler.fit_transform(df.values)

        model = IsolationForest(
            n_estimators=100,
            contamination=config.CONTAMINATION,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X)

        self.models[service]   = model
        self.scalers[service]  = scaler
        self.baseline_stats[service] = {
            "mean_latency": float(df["latency_ms"].mean()),
            "std_latency":  float(df["latency_ms"].std() + 1e-6),
            "error_rate":   float(df["is_error"].mean()),
            "n_samples":    len(training_logs),
        }
        self.last_trained[service] = datetime.utcnow()
        logger.info(f"[{service}] Model trained on {len(training_logs)} samples")
        return True

    def detect(self, service: str, recent_logs: List[dict]) -> List[AnomalyRecord]:
        if service not in self.models or not recent_logs:
            return []

        df = extract_features(recent_logs)
        X  = self.scalers[service].transform(df.values)

        predictions = self.models[service].predict(X)
        scores      = self.models[service].score_samples(X)
        stats       = self.baseline_stats[service]
        anomalies   = []

        for i, (pred, score) in enumerate(zip(predictions, scores)):
            if pred != -1 or score >= config.ANOMALY_SCORE_THRESHOLD:
                continue

            log          = recent_logs[i]
            features     = df.iloc[i].to_dict()
            anomaly_type = self._classify(features, stats)
            severity     = self._severity(score, features)
            explanation  = self._explain(features, stats, score)

            anomalies.append(AnomalyRecord(
                timestamp    = datetime.utcnow(),
                service      = service,
                anomaly_type = anomaly_type,
                score        = float(score),
                features     = {k: float(v) for k, v in features.items()},
                log_id       = log.get("id"),
                description  = f"{severity.upper()} {anomaly_type} in {service} (score={score:.3f})",
                severity     = severity,
                explanation  = explanation,
            ))

        return anomalies

    def _classify(self, features: dict, stats: dict) -> str:
        if features["is_error"] == 1.0:
            return "service_error"
        z = (features["latency_ms"] - stats["mean_latency"]) / stats["std_latency"]
        if z > 3:
            return "high_latency"
        return "statistical_anomaly"

    def _severity(self, score: float, features: dict) -> str:
        if features["is_error"] == 1.0 or score < -0.5:
            return "high"
        if score < -0.3:
            return "medium"
        return "low"

    def _explain(self, features: dict, stats: dict, score: float) -> dict:
        reasons = []
        if features["is_error"] == 1.0:
            reasons.append("Request resulted in an error status")
        z_lat = (features["latency_ms"] - stats["mean_latency"]) / stats["std_latency"]
        if z_lat > 2:
            reasons.append(
                f"Latency {features['latency_ms']:.0f}ms is {z_lat:.1f}σ above "
                f"baseline ({stats['mean_latency']:.0f}ms ± {stats['std_latency']:.0f}ms)"
            )
        if not reasons:
            reasons.append("Multivariate pattern deviates from trained baseline")
        return {
            "isolation_score": round(score, 4),
            "feature_values":  {k: round(v, 3) for k, v in features.items()},
            "baseline_stats":  {k: round(v, 3) if isinstance(v, float) else v
                                for k, v in stats.items()},
            "reasons": reasons,
        }


# ------------------------------------------------------------------ #
# Healing
# ------------------------------------------------------------------ #

# Global instances — shared across all requests and the monitoring loop
circuit_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=30)
rate_limiter    = RateLimiter(default_rate=10.0, burst=20)

# How many consecutive high-severity anomalies before opening the circuit
_consecutive_errors: Dict[str, int] = {}


async def trigger_healing(anomaly: AnomalyRecord, anomaly_id: int):
    """
    Healing action selection — type takes priority over severity.

    Mapping (addresses thesis Gap 2):
      service_error  + high   → restart_container
      service_error  + medium → monitor (log, watch)
      high_latency   + any    → rate_limit (throttle to 30%)
      statistical    + any    → monitor (log only)
    """
    service = anomaly.service
    action  = "monitor"
    details: dict = {}
    success = False

    if anomaly.anomaly_type == "service_error" and anomaly.severity == "high":
        action = "restart_container"
        try:
            resp = requests.post(
                "http://logging_service:8004/heal",
                json={
                    "service":      service,
                    "anomaly_type": anomaly.anomaly_type,
                    "severity":     anomaly.severity,
                    "score":        anomaly.score,
                    "description":  anomaly.description,
                    "action":       "restart_container",
                },
                timeout=10,
            )
            success = resp.status_code == 200
            details = resp.json() if success else {"status_code": resp.status_code}
        except Exception as e:
            details = {"error": str(e)}
            logger.error(f"[Healing] restart_container failed for {service}: {e}")

    elif anomaly.anomaly_type == "high_latency":
        action  = "rate_limit"
        details = rate_limiter.throttle(service, factor=0.3)
        success = True

    else:
        # statistical_anomaly OR service_error medium — monitor only
        action  = "monitor"
        details = {"reason": f"{anomaly.anomaly_type} ({anomaly.severity}) — monitoring"}
        success = True

    # Store healing action in DB
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()

        if success and anomaly_id:
            cursor.execute(
                "UPDATE detected_anomalies SET healed = TRUE WHERE id = %s",
                (anomaly_id,)
            )

        cursor.execute("""
            INSERT INTO healing_actions
            (anomaly_id, service, action, reason, success, details)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            anomaly_id, service, action,
            anomaly.description, success,
            json.dumps(details),
        ))

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"[Healing] Failed to store healing action: {e}")

# ------------------------------------------------------------------ #
# Background monitoring loop
# ------------------------------------------------------------------ #

async def monitoring_loop(app: FastAPI):
    while getattr(app.state, "running", True):
        logger.info("=== Monitoring cycle ===")
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT DISTINCT service FROM service_logs")
            services = [row["service"] for row in cursor.fetchall()]
            engine: AnomalyDetectionEngine = app.state.engine

            for service in services:
                # Training window: older than 5 min, up to 60 min back
                cursor.execute("""
                    SELECT * FROM service_logs
                    WHERE service = %s
                      AND timestamp > NOW() - INTERVAL %s
                      AND timestamp < NOW() - INTERVAL '5 minutes'
                    ORDER BY timestamp ASC LIMIT 2000
                """, (service, f"{config.ANALYSIS_WINDOW_MINUTES} minutes"))
                training_logs = [row_to_dict(r) for r in cursor.fetchall()]

                should_retrain = (
                    service not in engine.models or
                    (datetime.utcnow() - engine.last_trained.get(
                        service, datetime.min)).total_seconds() > 3600
                )
                if should_retrain:
                    engine.train(service, training_logs)

                # Detection window: last 5 minutes only
                cursor.execute("""
                    SELECT * FROM service_logs
                    WHERE service = %s
                      AND timestamp > NOW() - INTERVAL %s
                    ORDER BY timestamp ASC
                """, (service, f"{config.DETECTION_WINDOW_MINUTES} minutes"))
                recent_logs = [row_to_dict(r) for r in cursor.fetchall()]

                anomalies = engine.detect(service, recent_logs)
                for anomaly in anomalies:
                    anomaly_id = store_anomaly(anomaly, cursor)
                    conn.commit()
                    if anomaly_id is not None and anomaly.severity in ("high", "medium"):
                        await trigger_healing(anomaly, anomaly_id)

            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"Monitoring cycle error: {e}", exc_info=True)

        await asyncio.sleep(config.CHECK_INTERVAL_SECONDS)


# ------------------------------------------------------------------ #
# FastAPI app
# ------------------------------------------------------------------ #

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine  = AnomalyDetectionEngine()
    app.state.running = True
    create_anomaly_tables()
    task = asyncio.create_task(monitoring_loop(app))
    logger.info("Anomaly Detection Service started")
    yield
    app.state.running = False
    task.cancel()
    logger.info("Anomaly Detection Service stopped")


app = FastAPI(title="ML-Powered Anomaly Detection Service", lifespan=lifespan)
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
      CORSMiddleware,
      allow_origins=["http://localhost:3000"],   # tighten in production
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )

# ------------------------------------------------------------------ #
# Endpoints
# ------------------------------------------------------------------ #

@app.get("/health")
def health():
    engine: AnomalyDetectionEngine = app.state.engine
    return {
        "status":            "ok",
        "monitoring_active": getattr(app.state, "running", False),
        "models": {
            svc: {
                "last_trained":   engine.last_trained[svc].isoformat(),
                "baseline_stats": engine.baseline_stats[svc],
            }
            for svc in engine.models
        },
        "circuit_breakers": circuit_breaker.status(),
        "rate_limiters":    rate_limiter.status(),
    }


@app.get("/anomalies/recent")
def recent_anomalies(limit: int = 20,
                     severity: Optional[str] = None,
                     service: Optional[str] = None):
    conn   = get_db_connection()
    cursor = conn.cursor()

    filters, params = [], []
    if severity:
        filters.append("severity = %s"); params.append(severity)
    if service:
        filters.append("service = %s");  params.append(service)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(limit)
    cursor.execute(
        f"SELECT * FROM detected_anomalies {where} ORDER BY timestamp DESC LIMIT %s",
        params
    )
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close(); conn.close()
    return {"count": len(rows), "anomalies": rows}


@app.get("/healing/history")
def healing_history(limit: int = 20):
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ha.*, da.anomaly_type, da.severity
        FROM healing_actions ha
        JOIN detected_anomalies da ON ha.anomaly_id = da.id
        ORDER BY ha.executed_at DESC LIMIT %s
    """, (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close(); conn.close()
    return {"count": len(rows), "actions": rows}


@app.get("/circuit-breakers")
def get_circuit_breakers():
    """Current state of all circuit breakers — triggers auto-transitions."""
    for svc in list(circuit_breaker._state.keys()):
        circuit_breaker.get_state(svc)
    return {
        "circuit_breakers": circuit_breaker.status(),
        "rate_limiters":    rate_limiter.status(),
    }


@app.post("/circuit-breakers/{service}/reset")
def reset_circuit_breaker(service: str):
    """Manually reset a circuit breaker — for demo and testing."""
    circuit_breaker._state[service]         = CircuitBreaker.CLOSED
    circuit_breaker._failure_count[service] = 0
    circuit_breaker._success_count[service] = 0
    rate_limiter.restore(service)
    return {"message": f"Circuit breaker and rate limiter reset for {service}"}


@app.post("/detect/manual")
async def manual_detect():
    """Force an immediate detection cycle — for testing."""
    start  = datetime.utcnow()
    engine: AnomalyDetectionEngine = app.state.engine
    results = {}
    total   = 0

    try:
        conn   = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT DISTINCT service FROM service_logs")
        services = [r["service"] for r in cursor.fetchall()]

        for service in services:
            cursor.execute("""
                SELECT * FROM service_logs
                WHERE service = %s AND timestamp < NOW() - INTERVAL '2 minutes'
                ORDER BY timestamp ASC LIMIT 2000
            """, (service,))
            training_logs = [row_to_dict(r) for r in cursor.fetchall()]
            engine.train(service, training_logs)

            cursor.execute("""
                SELECT * FROM service_logs
                WHERE service = %s AND timestamp > NOW() - INTERVAL '10 minutes'
                ORDER BY timestamp ASC
            """, (service,))
            recent_logs = [row_to_dict(r) for r in cursor.fetchall()]

            anomalies = engine.detect(service, recent_logs)
            for a in anomalies:
                aid = store_anomaly(a, cursor)
                conn.commit()
                if aid is not None and a.severity in ("high", "medium"):
                    await trigger_healing(a, aid)

            results[service] = {
                "logs_analyzed":  len(recent_logs),
                "anomalies_found": len(anomalies),
            }
            total += len(anomalies)

        cursor.close(); conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success":         True,
        "processing_time_ms": int((datetime.utcnow() - start).total_seconds() * 1000),
        "total_anomalies": total,
        "results":         results,
        "circuit_breakers": circuit_breaker.status(),
        "rate_limiters":    rate_limiter.status(),
    }


@app.get("/stats")
def stats():
    conn   = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT severity, COUNT(*) as count, AVG(score) as avg_score
        FROM detected_anomalies GROUP BY severity
    """)
    by_severity = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT service,
               COUNT(*) as total,
               SUM(CASE WHEN healed THEN 1 ELSE 0 END) as healed,
               AVG(score) as avg_score
        FROM detected_anomalies GROUP BY service ORDER BY total DESC
    """)
    by_service = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT action, COUNT(*) as count,
               SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful
        FROM healing_actions GROUP BY action ORDER BY count DESC
    """)
    by_action = [dict(r) for r in cursor.fetchall()]

    cursor.close(); conn.close()

    engine: AnomalyDetectionEngine = app.state.engine
    return {
        "anomalies_by_severity": by_severity,
        "anomalies_by_service":  by_service,
        "healing_by_action":     by_action,
        "circuit_breakers":      circuit_breaker.status(),
        "rate_limiters":         rate_limiter.status(),
        "ml_models": {
            "trained_services": list(engine.models.keys()),
            "total_models":     len(engine.models),
        },
    }


@app.get("/db-test")
def db_test():
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as log_count FROM service_logs")
        count = cursor.fetchone()["log_count"]
        cursor.close(); conn.close()
        return {"status": "connected", "service_logs_count": count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def log_event(service, endpoint, method, request_body, response,
              start_time, status, error_message=None):
    event = {
        "service": service, "endpoint": endpoint, "method": method,
        "request": request_body if isinstance(request_body, dict) else {"body": str(request_body)},
        "response": response if isinstance(response, dict) else {"body": str(response)},
        "latency_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000),
        "status": status, "error_message": error_message,
    }
    try:
        requests.post("http://logging_service:8004/log", json=event, timeout=2)
    except Exception:
        pass

# ── Add these two endpoints to anomaly_detection_service/app.py ─────────────
# Place them alongside the existing /circuit-breakers endpoints

@app.post("/circuit-breakers/{service}/failure")
def record_cb_failure(service: str):
    """
    Called by the frontend when a downstream HTTP call fails.
    Wires real request-path failures into the circuit breaker state machine.
    """
    result = circuit_breaker.record_failure(service)
    logger.info(f"[CB] {service} failure recorded: {result}")
    return {
        "service": service,
        "action":  result["action"],
        "state":   result["state"],
        "failure_count": result["failure_count"],
    }


@app.post("/circuit-breakers/{service}/success")
def record_cb_success(service: str):
    """
    Called by the frontend when a downstream HTTP call succeeds.
    Allows circuit breaker to transition HALF_OPEN → CLOSED after recovery.
    """
    result = circuit_breaker.record_success(service)
    logger.info(f"[CB] {service} success recorded: {result}")
    return {
        "service": service,
        "action":  result["action"],
        "state":   result["state"],
    }

