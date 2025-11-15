from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class WeatherRequest(BaseModel):
    city: str

@app.get("/weather")
def get_weather(city: str):
    # For now, return a hardcoded response
    fake_weather = {
        "London": "sunny",
        "Paris": "rainy",
        "New York": "cloudy"
    }
    weather = fake_weather.get(city, "unknown")
    return {"city": city, "forecast": weather}
