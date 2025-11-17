# Weather Microservices Project

This project is a modular, microservice-based platform for delivering robust, explainable, and self-healing conversational weather services. The architecture is designed for extensibility, maintainability, and experimentation with modern microservice patterns.

## 🏗️ Project Structure

- `frontend/` — User-facing API gateway/chatbot (ACTIVE)
- `weather_service/` — Retrieves and formats weather data via external APIs (ACTIVE)
- `nlu_service/` — (Stub) Intended for advanced natural language understanding (future: extract cities/dates/intents from free text)
- `logging_service/` — (Stub) Will centralize application logs and metrics
- `anomaly_detection_service/` — (Stub) Will monitor system events and enable AI-driven anomaly detection & self-healing
- `notification_service/` — (Stub) For sending alerts to users or admins
- `cache_service/` — (Stub) For caching weather query results and speeding up responses
- `docker-compose.yml` — Orchestrates all services

## 🚀 Quick Start: Active Services

### 1️⃣ Weather Service
- Exposes `/weather?city=NAME`
- Docker: port 8001

### 2️⃣ Frontend (Gateway)
- User entrypoint, proxies requests to microservices
- Exposes `/weather?city=NAME`
- Docker: port 8000

> The remaining services are currently included as **stubs** for future expansion and do not provide real functionality yet.

## 📝 Running the System

1. **Clone the repository**
2. **(Optional)** Set up environment variables (see `docker-compose.yml` for details like your weather API key)
3. **Build and start active services (plus all skeletons):**
    ```
    docker-compose up --build
    ```
4. Visit:
    - Weather API: [http://localhost:8001/weather?city=London](http://localhost:8001/weather?city=London)
    - Frontend: [http://localhost:8000/weather?city=London](http://localhost:8000/weather?city=London)

5. **Stopping**
    ```
    docker-compose down
    ```

## 🏗️ Services in Development (Stubs)

- **`nlu_service/`**: Natural language query parsing
- **`logging_service/`**: Log/metrics aggregation and search
- **`anomaly_detection_service/`**: AI-driven anomaly detection, explainability & self-healing triggers
- **`notification_service/`**: Alerting and notification
- **`cache_service/`**: Caching and performance optimization

> Each stub has its own FastAPI/Docker skeleton in place so you can grow the architecture incrementally without restructuring the repository.


## API Keys and Secrets

1. Copy `.env.example` to `.env` and add your API keys.
2. The `docker-compose.yml` references environment variables such as `API_KEY` for local development.
3. On production or CI, set environment variables in your deployment system.

Example `.env`:
API_KEY=your_actual_key_here


## 🗺️ Roadmap

- Complete each microservice as project phases progress
- Integrate advanced monitoring, anomaly detection, and explainability
- Enable adaptive and autonomous remediation of faults

---

**Contributions and feedback welcome!**
