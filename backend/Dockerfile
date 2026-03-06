# LiveKit agent (single-repo deploy)
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# LiveKit agent runs as worker (long-running)
CMD ["python", "agent.py", "dev"]
