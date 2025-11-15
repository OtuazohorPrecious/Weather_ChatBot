import requests
print(requests.get("http://localhost:8000/weather?city=New York").json())
