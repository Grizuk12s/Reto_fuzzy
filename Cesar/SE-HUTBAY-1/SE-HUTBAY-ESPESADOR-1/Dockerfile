FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Envs de KEPserver (las de Flask ya no se usan: en producción manda Gunicorn)
ENV KEPSERVER_URL=opc.tcp://host.docker.internal:49320

# Gunicorn se configura en gunicorn.conf.py (bind, workers, threads, timeouts).
# Overrides puntuales por env: GUNICORN_BIND, GUNICORN_THREADS, GUNICORN_TIMEOUT.

EXPOSE 5000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
