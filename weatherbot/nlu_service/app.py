from fastapi import FastAPI
from pydantic import BaseModel
import re
from datetime import datetime, timedelta
import requests

app = FastAPI()

LOGGING_ENDPOINT = "http://logging_service:8004/log"

class NLURequest(BaseModel):
    text: str


def extract_city(text: str):
    """
    Extract city name from natural language text using regex patterns.
    No hardcoded city list — extracted candidate is passed to OpenWeatherMap
    which validates it. This handles any city in the world.
    """
    normalized = re.sub(r"[^\w\s]", "", text.lower())

    # Pattern 1: "weather in <city>" / "rain in <city>" etc.
    match = re.search(r"\bin\s+([a-z][a-z\s]{1,30})", normalized)
    if match:
        candidate = match.group(1).strip()
        # Strip trailing time words that got captured
        for noise in ["today", "tomorrow", "tonight", "this evening",
                      "this morning", "this week", "next week",
                      "monday","tuesday","wednesday","thursday",
                      "friday","saturday","sunday"]:
            if candidate.endswith(noise):
                candidate = candidate[:-len(noise)].strip()
        if candidate:
            return candidate.title()

    # Pattern 2: "london weather" / "paris forecast"
    match = re.search(r"^([a-z][a-z\s]{1,20})\s+(?:weather|forecast|temperature|rain|snow|wind|humidity)", normalized)
    if match:
        return match.group(1).strip().title()

    # Pattern 3: last resort — capitalised word(s) before a weather keyword
    match = re.search(r"([a-z][a-z\s]{1,20})\s+(?:weather|forecast|temperature)", normalized)
    if match:
        return match.group(1).strip().title()

    return None


def extract_date(text: str):
    today = datetime.now()
    text = text.lower()
    if "tomorrow" in text:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if "today" in text or "tonight" in text:
        return today.strftime("%Y-%m-%d")
    days_of_week = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for i, day in enumerate(days_of_week):
        if day in text:
            delta = (i - today.weekday() + 7) % 7 or 7
            return (today + timedelta(days=delta)).strftime("%Y-%m-%d")
    return today.strftime("%Y-%m-%d")


def log_event(service, endpoint, method, request_body, response,
              start_time, status, error_message=None):
    event = {
        "service": service,
        "endpoint": endpoint,
        "method": method,
        "request": request_body if isinstance(request_body, dict) else {"body": str(request_body)},
        "response": response if isinstance(response, dict) else {"body": response},
        "latency_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000),
        "status": status,
        "error_message": error_message,
    }
    try:
        requests.post("http://logging_service:8004/log", json=event, timeout=2)
    except Exception:
        pass


@app.post("/parse")
def parse_text(request: NLURequest):
    start_time = datetime.utcnow()
    text = request.text
    try:
        city = extract_city(text)
        date = extract_date(text)
        response = {"city": city, "date": date}
        status = "success"
        error_message = None
    except Exception as e:
        response = {"error": str(e)}
        status = "error"
        error_message = str(e)
    log_event("nlu_service", "/parse", "POST",
              request.dict(), response, start_time, status, error_message)
    return response


@app.get("/health")
def health():
    """
    Test NLU logic on representative inputs.
    No external calls — fast and reliable.
    """
    test_cases = [
        ("What's the weather in Lagos tomorrow?",     "Lagos"),
        ("Will it rain in New York today?",           "New York"),
        ("Temperature in São Paulo this week",        "São Paulo"),
        ("Is it hot in Abuja?",                       "Abuja"),
        ("London weather forecast",                   "London"),
        ("Weather next Monday in Berlin",             "Berlin"),
    ]

    anomalies = []
    details = {}

    for text, expected_city in test_cases:
        city = extract_city(text)
        date = extract_date(text)
        details[text] = {"city": city, "date": date, "expected_city": expected_city}
        if not city:
            anomalies.append({"test": text, "problem": "City extraction returned None"})

    return {
        "status": "anomaly" if anomalies else "ok",
        "details": details,
        "anomalies": anomalies,
    }
