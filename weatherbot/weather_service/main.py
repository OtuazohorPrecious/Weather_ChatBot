from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os
import datetime

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


app = FastAPI()

API_KEY = os.environ.get("API_KEY")



@app.get("/")
def read_root():
    return {"message": "Welcome! Use /weather?city=CityName or visit /docs for API documentation."}


@app.get("/weather")
def get_weather(city: str):
    start_time = datetime.datetime.utcnow()
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric"
        response = requests.get(url)
        data = response.json()
        # Check for valid city
        if data.get("cod") != 200:
            result = {
                "city": city,
                "forecast": None,
                "temperature": None,
                "error": data.get("message", "City not found")
            }
            status = "error"
            error_message = result["error"]
        else:
            forecast = data.get('weather', [{}])[0].get('main', "Unknown")
            temp = data.get('main', {}).get('temp', None)
            result = {
                "city": city,
                "forecast": forecast,
                "temperature": temp
            }
            status = "success"
            error_message = None
    except requests.exceptions.RequestException as e:
        result = {
            "city": city,
            "forecast": None,
            "temperature": None,
            "error": "External weather service unavailable. Please try again later."
        }
        status = "error"
        error_message = str(e)
    log_event("weather_service", "/weather", "GET", {"city": city}, result, start_time, status, error_message)
    return result

    


@app.get("/health")
def health():
    import os
    import requests

    status = "ok"
    anomalies = []
    details = {}

    api_key = os.environ.get("API_KEY")
    
    # Check if API key is present
    if not api_key or api_key.strip() == "":
        anomalies.append({"problem": "Missing API_KEY environment variable"})
        details["api_key"] = "missing"
    
    # Test a list of representative cities (customize as needed)
    test_cities = ["London", "Nowhereland", "Lagos"]
    for city in test_cities:
        try:
            url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
            response = requests.get(url, timeout=4)
            data = response.json()
            detail = {"status_code": response.status_code, "body": data}
            if response.status_code != 200 or data.get("cod") != 200:
                anomalies.append({"city": city, "problem": "Bad response", "response": data})
            if "error" in data or data.get("cod") and data.get("cod") != 200:
                anomalies.append({"city": city, "problem": "Error from API", "response": data})
            details[city] = detail
        except Exception as e:
            anomalies.append({"city": city, "problem": f"Exception: {e}"})
            details[city] = {"error": str(e)}

    if anomalies:
        status = "anomaly"
    return {
        "status": status,
        "details": details,
        "anomalies": anomalies
    }



class WeatherRequest(BaseModel):
    city: str

# @app.get("/weather")
# def get_weather(city: str):
#     # For now, return a hardcoded response
#     fake_weather = {
#         "London": "sunny",
#         "Paris": "rainy",
#         "New York": "cloudy"
#     }
#     weather = fake_weather.get(city, "unknown")
#     return {"city": city, "forecast": weather}
