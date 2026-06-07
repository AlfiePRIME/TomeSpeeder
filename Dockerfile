FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY web/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY web /app/web

ENV LIBRARY_ROOT=/audiobooks \
    PYTHONUNBUFFERED=1
EXPOSE 8080

# Use bind 0.0.0.0 so the container is reachable; one worker keeps the in-memory
# job registry and locks coherent.
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
