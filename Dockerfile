# ---- Base ----
FROM python:3.11-slim

# Prevent .pyc, force unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Workdir
WORKDIR /app

# ---- System deps (build + minimal runtime) ----
# If you add scientific libs later (numpy/scipy/astropy), keep build-essential.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
 && rm -rf /var/lib/apt/lists/*

# ---- Python deps (cache-friendly) ----
# (This layer only invalidates when requirements.txt changes)
COPY requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt

# ---- App code ----
COPY . .

# ---- Runtime config ----
EXPOSE 5001

# Optional: make Gunicorn a tad more resilient in containers
# - gthread works well for Flask + light I/O
# - --timeout 30 avoids premature kills on cold starts
# - --worker-tmp-dir /dev/shm avoids tmpfs issues on some hosts
CMD ["gunicorn", "-w", "2", "-k", "gthread", "--threads", "4", "--timeout", "30", "--worker-tmp-dir", "/dev/shm", "--log-level", "info", "-b", "0.0.0.0:5001", "nova:app"]