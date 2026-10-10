# Research AI Agent - web app image
# Works on Intel/AMD (x86_64) and Arm (e.g. Oracle Ampere) servers.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the code
COPY . .

EXPOSE 8000

# Docker marks the container "healthy" once /health answers
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

# --host 0.0.0.0 so the app is reachable from outside the container
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
