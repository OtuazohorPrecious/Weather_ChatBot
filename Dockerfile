# Use the official Python 3.10 image from Docker Hub
FROM python:3.10

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and main.py into the container
COPY requirements.txt .
COPY main.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port FastAPI will run on
EXPOSE 8000

# Set the command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
