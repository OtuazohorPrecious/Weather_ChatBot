#!/bin/sh
# Inject runtime environment variables into the built index.html
# This allows API URLs to be set via docker-compose environment: without rebuilding

ANOMALY_URL="${ANOMALY_API_URL:-http://localhost:8002}"
LOGGING_URL="${LOGGING_API_URL:-http://localhost:8004}"

sed -i "s|VITE_ANOMALY_API_URL_PLACEHOLDER|${ANOMALY_URL}|g" /usr/share/nginx/html/index.html
sed -i "s|VITE_LOGGING_API_URL_PLACEHOLDER|${LOGGING_URL}|g" /usr/share/nginx/html/index.html

echo "Dashboard configured:"
echo "  ANOMALY_API_URL = ${ANOMALY_URL}"
echo "  LOGGING_API_URL = ${LOGGING_URL}"

exec "$@"
