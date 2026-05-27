
from fastapi import FastAPI, Request
import requests
import os
import datetime

app = FastAPI()

NLU_URL = "http://nlu_service:8005/parse"
WEATHER_URL = "http://weather_service:8001/weather"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY environment variable is not set")
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Choose a supported model (OpenChat, Mixtral, etc.)
MODEL = "x-ai/grok-4.1-fast"  # Try also: "mistralai/mixtral-8x7b-instruct" or others from https://openrouter.ai/models


LOGGING_ENDPOINT = "http://logging_service:8004/log"

def log_event(service, endpoint, method, request_body, response, start_time, status, error_message=None):
    """Send logs to the centralized logging service with new format"""
    event = {
        "service": service,
        "endpoint": endpoint,
        "method": method,
        "request": request_body if isinstance(request_body, dict) else {"body": str(request_body)},
        "response": response if isinstance(response, dict) else {"body": response},
        "latency_ms": int((datetime.datetime.utcnow() - start_time).total_seconds() * 1000),
        "status": status,
        "error_message": error_message
    }
    try:
        # Point to the new logging service
        requests.post("http://logging_service:8004/log", json=event, timeout=2)
    except Exception:
        pass  # Don't crash if logging fails



@app.post("/respond")
async def respond(request: Request):
    start_time = datetime.datetime.utcnow()

    try:
        # FIX: Handle potential encoding issues with the request body
        body_bytes = await request.body()
        
        # Try UTF-8, fallback to latin-1 for the degree symbol
        try:
            body_str = body_bytes.decode('utf-8')
        except UnicodeDecodeError:
            body_str = body_bytes.decode('latin-1')
            
        import json
        data = json.loads(body_str)
        
    except Exception as e:
        # If request parsing fails, return error
        error_msg = f"Error parsing request: {e}"
        log_event("responder_service", "/respond", "POST", {"error": str(e)}, {"response": error_msg}, start_time, "error", error_msg)
        return {"response": error_msg}
    
    user_query = data.get("text", "")
    weather_facts = data.get("weather", "")
    
    # FIX: Clean weather_facts to remove any problematic characters
    if weather_facts:
        # Remove degree symbol and other non-ASCII characters for safety
        weather_facts = weather_facts.replace('°', '').replace('º', '').strip()
    #data = await request.json()
    prompt = (
        f"User asked: '{user_query}'. "
        f"The latest weather forecast: {weather_facts}. "
        "Reply as a helpful assistant in fluent English, one sentence."
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        if "choices" in result:
            generated = result["choices"][0]["message"]["content"].strip()
            final_response = {"response": generated}
            status = "success"
            error_message = None
        elif "error" in result:
            final_response = {"response": f"API Error: {result['error']}"}
            status = "error"
            error_message = result['error']
        else:
            final_response = {"response": f"Unexpected API response: {result}"}
            status = "error"
            error_message = "Unexpected API response"
    except Exception as e:
        final_response = {"response": f"Error: {e}"}
        status = "error"
        error_message = str(e)
    log_event("responder_service", "/respond", "POST", data, final_response, start_time, status, error_message)
    return final_response

    

@app.get("/health")
def health():
    """
    Lightweight health check — does NOT call the LLM.
    Verifies NLU and weather dependencies are reachable.
    """
    anomalies = []
    details = {}

    deps = {
        "nlu":     "http://nlu_service:8005/health",
        "weather": "http://weather_service:8001/health",
    }
    for name, url in deps.items():
        try:
            res = requests.get(url, timeout=3)
            details[name] = {"status": res.status_code}
            if res.status_code != 200:
                anomalies.append({"dependency": name, "problem": f"HTTP {res.status_code}"})
        except Exception as e:
            anomalies.append({"dependency": name, "problem": str(e)})
            details[name] = {"status": "unreachable"}

    return {
        "status": "anomaly" if anomalies else "ok",
        "details": details,
        "anomalies": anomalies,
    }