from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os


app = FastAPI()

API_KEY = os.environ.get("API_KEY")



@app.get("/")
def read_root():
    return {"message": "Welcome! Use /weather?city=CityName or visit /docs for API documentation."}


@app.get("/weather")
def get_weather(city: str):
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric"
        response = requests.get(url)
        data = response.json()

        # Check for valid city
        if data.get("cod") != 200:
            return {
                "city": city,
                "forecast": None,
                "temperature": None,
                "error": data.get("message", "City not found")
            }
        
        # Extract useful info (simplified)
        forecast = data.get('weather', [{}])[0].get('main', "Unknown")
        temp = data.get('main', {}).get('temp', None)
        return {
            "city": city,
            "forecast": forecast,
            "temperature": temp
        }
    except requests.exceptions.RequestException as e:  # 
        print(f"Error: {e}")  # This logs to console/server, for developer debugging
        return {
            "city": city,
            "forecast": None,
            "temperature": None,
            "error": "External weather service unavailable. Please try again later."
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
