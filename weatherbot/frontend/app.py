from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import requests
import datetime

LOGGING_ENDPOINT = "http://logging_service:8004/log"
NLU_URL          = "http://nlu_service:8005/parse"
WEATHER_URL      = "http://weather_service:8001/weather"
RESPONDER_URL    = "http://responder_service:8007/respond"
ANOMALY_URL      = "http://anomaly_detection_service:8002"

# ── Lightweight circuit breaker client ──────────────────────────────────────
# The frontend records successes and failures to the anomaly_detection_service
# circuit breaker via HTTP.  This wires real request outcomes into the CB state.

def cb_record_failure(service: str):
    """Tell the anomaly detection service that a downstream call failed."""
    try:
        requests.post(
            f"{ANOMALY_URL}/circuit-breakers/{service}/failure",
            timeout=1
        )
    except Exception:
        pass   # never crash the frontend because of CB reporting

def cb_record_success(service: str):
    """Tell the anomaly detection service that a downstream call succeeded."""
    try:
        requests.post(
            f"{ANOMALY_URL}/circuit-breakers/{service}/success",
            timeout=1
        )
    except Exception:
        pass

def cb_is_open(service: str) -> bool:
    """Returns True if the circuit breaker is OPEN for this service."""
    try:
        r = requests.get(f"{ANOMALY_URL}/circuit-breakers", timeout=1)
        state = r.json().get("circuit_breakers", {}).get(service, {}).get("state", "closed")
        return state == "open"
    except Exception:
        return False   # fail open — don't block requests if CB check fails

# ── Logging helper ───────────────────────────────────────────────────────────

def log_event(service, endpoint, method, request_body, response,
              start_time, status, error_message=None):
    event = {
        "service":       service,
        "endpoint":      endpoint,
        "method":        method,
        "request":       request_body if isinstance(request_body, dict) else {"body": str(request_body)},
        "response":      response if isinstance(response, dict) else {"body": response},
        "latency_ms":    int((datetime.datetime.utcnow() - start_time).total_seconds() * 1000),
        "status":        status,
        "error_message": error_message
    }
    try:
        requests.post("http://logging_service:8004/log", json=event, timeout=2)
    except Exception:
        pass

# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    start_time = datetime.datetime.utcnow()
    html_result = """
    <html>
        <head><title>Weather Chatbot Demo</title></head>
        <body>
            <h2>WeatherBot</h2>
            <form action="/weather_form" method="post">
                <input type="text" name="text" size="40"
                       placeholder="e.g. Will it rain in Lagos tomorrow?"/>
                <input type="submit" value="Ask">
            </form>
        </body>
    </html>
    """
    log_event("frontend_service", "/", "GET", {}, html_result,
              start_time, "success")
    return html_result


@app.post("/weather_form", response_class=HTMLResponse)
async def weather_form(request: Request):
    start_time = datetime.datetime.utcnow()
    try:
        form = await request.form()
        text = form["text"]

        # ── 1. NLU ──────────────────────────────────────────────────────────
        if cb_is_open("nlu_service"):
            error_html = "<b>Service unavailable:</b> NLU service circuit breaker is OPEN. <a href='/'>Try again shortly</a>"
            log_event("frontend", "/weather_form", "POST", {"text": text},
                      error_html, start_time, "error", "circuit_breaker_open:nlu_service")
            return error_html

        try:
            nlu_resp = requests.post(NLU_URL, json={"text": text}, timeout=5)
            nlu_data = nlu_resp.json()
            cb_record_success("nlu_service")
        except Exception as e:
            cb_record_failure("nlu_service")
            error_html = f"<b>Error:</b> NLU service unavailable. <a href='/'>Try again</a>"
            log_event("frontend", "/weather_form", "POST", {"text": text},
                      error_html, start_time, "error", str(e))
            return error_html

        city = nlu_data.get("city")
        if not city:
            error_html = f"<b>Error:</b> Could not extract city from '{text}'. <a href='/'>Try again</a>"
            log_event("frontend", "/weather_form", "POST", {"text": text},
                      error_html, start_time, "error", "City not extracted")
            return error_html

        # ── 2. Weather service ───────────────────────────────────────────────
        if cb_is_open("weather_service"):
            error_html = "<b>Service unavailable:</b> Weather service circuit breaker is OPEN. <a href='/'>Try again shortly</a>"
            log_event("frontend", "/weather_form", "POST", {"text": text},
                      error_html, start_time, "error", "circuit_breaker_open:weather_service")
            return error_html

        try:
            weather_resp = requests.get(f"{WEATHER_URL}?city={city}", timeout=5)
            weather_data = weather_resp.json()
            cb_record_success("weather_service")
        except Exception as e:
            cb_record_failure("weather_service")
            error_html = f"<b>Error:</b> Weather service unavailable. <a href='/'>Try again</a>"
            log_event("frontend", "/weather_form", "POST", {"text": text},
                      error_html, start_time, "error", str(e))
            return error_html

        weather_facts = (f"{weather_data.get('forecast','Unknown')}, "
                         f"{weather_data.get('temperature','N/A')}°C in {city}")

        # ── 3. Responder ─────────────────────────────────────────────────────
        if cb_is_open("responder_service"):
            # Graceful degradation: return raw facts without LLM response
            html_result = f"""
                <h3>You asked:</h3><p>{text}</p>
                <h3>WeatherBot Answer:</h3>
                <p><em>(LLM service temporarily unavailable — showing raw data)</em></p>
                <h4>Raw facts:</h4><pre>{weather_data}</pre>
                <a href='/'>Another query</a>
            """
            log_event("frontend", "/weather_form", "POST", {"text": text},
                      html_result, start_time, "success",
                      "circuit_breaker_open:responder_service")
            return html_result

        try:
            resp_resp = requests.post(
                RESPONDER_URL, json={"text": text, "weather": weather_facts},
                timeout=30
            )
            resp_data = resp_resp.json()
            cb_record_success("responder_service")
        except Exception as e:
            cb_record_failure("responder_service")
            resp_data = {"response": f"LLM service unavailable: {e}"}

        response_statement = resp_data.get("response", "Unable to generate response.")

        html_result = f"""
            <h3>You asked:</h3><p>{text}</p>
            <h3>WeatherBot Answer:</h3><p>{response_statement}</p>
            <h4>Raw facts:</h4><pre>{weather_data}</pre>
            <a href='/'>Another query</a>
        """

        log_event("frontend", "/weather_form", "POST", {"text": text},
                  {"nlu_data": nlu_data, "weather_data": weather_data,
                   "resp_data": resp_data},
                  start_time, "success")
        return html_result

    except Exception as e:
        error_html = f"<b>Error:</b> {e} <a href='/'>Try again</a>"
        log_event("frontend", "/weather_form", "POST", {}, error_html,
                  start_time, "error", str(e))
        return error_html


@app.get("/health")
def health():
    start_time = datetime.datetime.utcnow()
    status = "ok"
    details = {}
    anomalies = []

    deps = {
        "nlu":       "http://nlu_service:8005/health",
        "weather":   "http://weather_service:8001/health",
        "responder": "http://responder_service:8007/health",
    }
    for name, url in deps.items():
        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                details[name] = {"status": "ok"}
            else:
                anomalies.append({"dependency": name,
                                  "problem": f"HTTP {resp.status_code}"})
                details[name] = {"status": resp.status_code}
        except requests.exceptions.Timeout:
            details[name] = {"status": "timeout"}
        except Exception as e:
            anomalies.append({"dependency": name, "problem": str(e)})
            details[name] = {"status": "unreachable"}

    if anomalies:
        status = "anomaly"

    response = {"status": status, "details": details, "anomalies": anomalies}
    log_event("frontend_service", "/health", "GET", {}, response,
              start_time, status, None)
    return response
