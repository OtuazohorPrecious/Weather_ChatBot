# logging_service/app.py - REFACTORED VERSION
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
import json 
import docker  # NEW: Docker SDK
from databases import Database  # NEW: Async database
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from typing import Optional, Union, Dict, Any
from pydantic_settings import BaseSettings#, pydantic-settings #BaseSettings
from sqlalchemy import Column, DateTime, Integer, String, create_engine, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import subprocess
import os

#logger = logging.getLogger("logging_service")
# --- Configuration using Pydantic --- # NEW
class Settings(BaseSettings):
    database_url: str = f"postgresql://postgres:{os.getenv('POSTGRES_PASSWORD', 'password')}@log_db/logdb"
    service_to_container_map: dict = {
        "weather_service": "weather_service",
        "nlu_service": "weatherbot-nlu_service-1",
        "frontend": "frontend",
        "responder_service": "weatherbot-responder_service-1",
        "anomaly_detection_service": "weatherbot-anomaly_detection_service-1"
    }

    class Config:
        env_file = ".env"

settings = Settings()

# --- Database Setup --- # NEW
DATABASE_URL = settings.database_url
database = Database(DATABASE_URL)
Base = declarative_base()
engine = create_engine(DATABASE_URL)

# SQLAlchemy Model
class ServiceLog(Base):
    __tablename__ = "service_logs"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    service = Column(String, nullable=False)
    endpoint = Column(String)
    method = Column(String)
    request = Column(JSON)
    response = Column(JSON)
    latency_ms = Column(Integer)
    status = Column(String)
    error_message = Column(String)

# --- Pydantic Schemas for API --- # NEW
class LogCreate(BaseModel):
    service: str
    endpoint: Optional[str] = None
    method: Optional[str] = None
    request: Optional[dict] = None
    response: Optional[dict] = None
    latency_ms: Optional[int] = None
    status: str
    error_message: Optional[str] = None


class LogResponse(BaseModel):
    id: int
    timestamp: datetime
    service: str
    endpoint: Optional[str] = None
    method: Optional[str] = None
    request: Optional[Union[dict, str]] = None
    response: Optional[Union[dict, str]] = None
    latency_ms: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    
    class Config:
        from_attributes = True

# --- Core Business Logic --- #
class HealingOrchestrator:
    """Manages detection and healing decisions"""
    def __init__(self):
        self.docker_client = docker.from_env()  # NEW: Proper Docker client
        self.service_map = self._build_service_map()

    def _build_service_map(self):
        """Map service names to container names using labels."""
        service_map = {}
        containers = self.docker_client.containers.list()
        for container in containers:
            labels = container.labels
            # Expect a label like "com.docker.compose.service" for compose projects
            if "com.docker.compose.service" in labels:
                service_name = labels["com.docker.compose.service"]
                service_map[service_name] = container.name
        return service_map

    async def detect_anomalies(self, recent_logs: List[dict]) -> List[dict]:
        """Analyze logs for anomalies using rule-based and ML logic"""
        anomalies = []
        
        # Rule 1: Service Errors
        for log in recent_logs[-20:]:  # Last 20 logs
            if log['status'] == 'error':
                anomalies.append({
                    'type': 'service_error',
                    'service': log['service'],
                    'log_id': log['id'],
                    'reason': log.get('error_message', 'Unknown error'),
                    'severity': 'high'
                })
        
        # Rule 2: High Latency (Simple Statistical ML)
        latencies = [l['latency_ms'] for l in recent_logs if l.get('latency_ms')]
        if len(latencies) > 10:
            import numpy as np
            avg = np.mean(latencies)
            std = np.std(latencies)
            for log in recent_logs[-10:]:
                latency = log.get('latency_ms')
                if latency and (latency > avg + 3 * std) and latency > 500:
                    anomalies.append({
                        'type': 'high_latency',
                        'service': log['service'],
                        'log_id': log['id'],
                        'reason': f'Latency {latency}ms > {avg:.0f}ms + 3σ',
                        'severity': 'medium'
                    })
        
        return anomalies

    async def execute_healing(self, anomaly: dict) -> dict:
        """
        Execute the healing action specified in the anomaly dict.
        The anomaly_detection_service decides WHAT action to take.
        This service just executes it using Docker.
        """
        service        = anomaly['service']
        action         = anomaly.get('action', 'monitor')
        container_name = self.service_map.get(service)

        if not container_name:
            # Try to find it dynamically
            self.service_map = self._build_service_map()
            container_name   = self.service_map.get(service)

        if not container_name:
            return {'success': False, 'error': f'No container mapping for {service}'}

        try:
            container = self.docker_client.containers.get(container_name)

            if action == "restart_container":
                container.restart()
                logger.info(f"Restarted container {container_name}")
                return {'success': True, 'action': 'restart_container',
                        'container': container_name}

            elif action == "circuit_open":
                # Circuit is open — just log it, no Docker action needed
                return {'success': True, 'action': 'circuit_open',
                        'container': container_name,
                        'note': 'Circuit breaker opened, requests being blocked'}

            elif action == "rate_limit":
                # Rate limiting is handled in anomaly_detection_service
                # Nothing to do at the Docker level
                return {'success': True, 'action': 'rate_limit',
                        'container': container_name}

            else:
                # monitor or unknown — just acknowledge
                return {'success': True, 'action': 'monitor',
                        'container': container_name}

        except docker.errors.NotFound:
            return {'success': False, 'error': f'Container {container_name} not found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

# --- FastAPI App with Lifespan Management --- # NEW
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Connecting to database...")
    await database.connect()
    Base.metadata.create_all(bind=engine)  # Create tables if they don't exist
    
    # Start background monitoring task
    orchestrator = HealingOrchestrator()
    monitor_task = asyncio.create_task(background_monitor(orchestrator))
    
    yield
    
    # Shutdown
    monitor_task.cancel()
    await database.disconnect()

app = FastAPI(title="Self-Healing Logging Service", lifespan=lifespan)
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
      CORSMiddleware,
      allow_origins=["http://localhost:3000"],   # tighten in production
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )

orchestrator = HealingOrchestrator()

# --- Background Task --- # NEW
async def background_monitor(orchestrator: HealingOrchestrator):
    """
    Disabled — anomaly detection is now handled exclusively by
    anomaly_detection_service. This function is kept as a stub
    to avoid breaking the lifespan startup code.
    """
    while True:
        await asyncio.sleep(3600)
# --- API Endpoints --- #
@app.post("/log", response_model=LogResponse, status_code=status.HTTP_201_CREATED)
async def create_log(log: LogCreate):
    """Receive and store logs from services"""
    # Convert Pydantic model to dict
    values = log.dict()
    
    # CRITICAL FIX: Convert dicts to JSON strings for PostgreSQL
    if isinstance(values.get('request'), dict):
        values['request'] = json.dumps(values['request'])
    if isinstance(values.get('response'), dict):
        values['response'] = json.dumps(values['response'])
    
    query = """
    INSERT INTO service_logs 
    (service, endpoint, method, request, response, latency_ms, status, error_message)
    VALUES (:service, :endpoint, :method, :request, :response, :latency_ms, :status, :error_message)
    RETURNING id, timestamp
    """
    
    try:
        result = await database.fetch_one(query, values=values)
        
        # Convert to dict and add the returned fields
        log_data = {**log.dict(), "id": result["id"], "timestamp": result["timestamp"]}
        print(f"📝 Log saved: {log_data['service']} - {log_data['status']}")
        
        return log_data
    except Exception as e:
        print(f"❌ Database insert error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    


@app.get("/logs", response_model=List[LogResponse])
async def get_logs(limit: int = 100):
    """Retrieve recent logs"""
    query = "SELECT * FROM service_logs ORDER BY timestamp DESC LIMIT :limit"
    rows = await database.fetch_all(query, values={"limit": limit})
    
    # Convert rows to proper response format
    logs = []
    for row in rows:
        log_dict = dict(row)
        # Parse JSON strings back to Python dicts
        if isinstance(log_dict.get('request'), str):
            try:
                log_dict['request'] = json.loads(log_dict['request'])
            except:
                pass  # Keep as string if not valid JSON   
        if isinstance(log_dict.get('response'), str):
            try:
                log_dict['response'] = json.loads(log_dict['response'])
            except:
                pass
        logs.append(log_dict)
    return logs

@app.get("/ml_stats")
async def get_ml_stats():
    """Show statistics for ML analysis"""
    query = """
    SELECT 
        COUNT(*) as total_logs,
        AVG(latency_ms) as avg_latency,
        STDDEV(latency_ms) as latency_stddev,
        COUNT(CASE WHEN status='error' THEN 1 END) as error_count
    FROM service_logs
    WHERE latency_ms IS NOT NULL
    """
    stats = await database.fetch_one(query)
    
    return {
        "total_logs": stats["total_logs"],
        "avg_latency": f"{stats['avg_latency'] or 0:.1f}ms",
        "latency_stddev": f"{stats['latency_stddev'] or 0:.1f}ms",
        "error_rate": f"{(stats['error_count'] or 0) / (stats['total_logs'] or 1) * 100:.1f}%",
        "ml_ready": stats["total_logs"] > 50  # Indicate if we have enough data
    }

@app.get("/anomalies")
async def get_recent_anomalies():
    """Get recently detected anomalies (for dashboard)"""
    # This would query a separate anomalies table
    return {"message": "Anomaly endpoint ready for future implementation"}

@app.get("/health")
async def health_check():
    """Comprehensive health check"""
    try:
        # Check database
        await database.execute("SELECT 1")
        db_status = "healthy"
    except:
        db_status = "unhealthy"
    
    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "details": {
            "database": db_status,
            "ml_active": True,
            "healing_enabled": True
        }
    }

@app.post("/heal")
async def heal_service(request: Request):
    """
    Endpoint for anomaly detection service to trigger healing actions.
    Expects JSON with at least 'service' and 'severity'.
    """
    start_time = datetime.utcnow()
    try:
        data = await request.json()
        service = data.get("service")
        anomaly_type = data.get("anomaly_type", "unknown")
        severity = data.get("severity", "medium")
        description = data.get("description", "")

        # Build the anomaly dict expected by HealingOrchestrator.execute_healing
        anomaly = {
            'service': service,
            'type': anomaly_type,
            'severity': severity,
            'reason': description or f"Healing triggered for {anomaly_type}"
        }

        # Execute healing (restart container for high severity, monitor otherwise)
        result = await orchestrator.execute_healing(anomaly)

        response = {
            "success": result.get('success', False),
            "action": result.get('action', 'none'),
            "message": f"Healing attempted for {service}",
            "details": result
        }
        status = "success" if result.get('success') else "partial"
        error_message = None

    except Exception as e:
        response = {"success": False, "error": str(e)}
        status = "error"
        error_message = str(e)

    return response

