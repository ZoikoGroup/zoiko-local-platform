FROM python:3.10-slim

WORKDIR /app

# psycopg2-binary ships its own libpq, so no build-essential needed here -
# curl is just for the container healthcheck below.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# alembic.ini and alembic/ live inside this directory alongside app/, so a
# plain COPY . . picks up everything needed to both serve the API and run
# migrations from this same image (e.g. `docker run <image> alembic upgrade head`).
COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
