FROM python:3.10-slim

WORKDIR /app

COPY . .

#RUN pip install --no-cache-dir -r requirements1.txt

# Install system dependencies for PostgreSQL client and ML libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ postgresql-client libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies with increased timeout and retries
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt \
        --timeout 100 \
        --retries 5 


# # Expose the port FastAPI will run on
# EXPOSE 8004

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8004"]