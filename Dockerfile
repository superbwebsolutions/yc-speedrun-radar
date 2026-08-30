FROM python:3.12-slim

WORKDIR /app

# Dependency install (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# State lives in /app/data — mount a volume to persist across restarts
RUN mkdir -p data
VOLUME ["/app/data"]

# Pond health-check server port
EXPOSE 8000

# One process does both: the monitor loop + the Pond HTTP server.
CMD ["python", "-u", "manage.py", "poll"]
