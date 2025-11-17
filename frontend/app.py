from fastapi import FastAPI
import requests

app = FastAPI()

WEATHER_SERVICE_URL = "http://weather_service:8001/weather"


@app.get("/")
def read_root():
    return {"message": "Welcome! Use /weather?city=CityName or visit /docs for API documentation."}


@app.get("/weather")
def user_weather(city: str):
    try:
        response = requests.get(f"{WEATHER_SERVICE_URL}?city={city}")
        return response.json()
    except Exception as e:
        print(f"Error: {e}")
        return {"city": city, "error": "Weather service not available"}
