# ---- Base ----
[cite_start]FROM python:3.11-slim [cite: 2]

# Prevent .pyc, force unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    [cite_start]PIP_NO_CACHE_DIR=1 [cite: 2]

# Workdir
[cite_start]WORKDIR /app [cite: 2]

# ---- System deps (build + minimal runtime) ----
# If you add scientific libs later (numpy/scipy/astropy), keep build-essential.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
 [cite_start]&& rm -rf /var/lib/apt/lists/* [cite: 3]

# ---- Python deps (cache-friendly) ----
# (This layer only invalidates when requirements.txt changes)
[cite_start]COPY requirements.txt . [cite: 2]
RUN python -m pip install --upgrade pip setuptools wheel \
 [cite_start]&& pip install -r requirements.txt [cite: 4]

# ---- App code ----
COPY . [cite_start]. [cite: 5]

# ---- Runtime config ----
[cite_start]EXPOSE 5001 [cite: 2]

# Optional: make Gunicorn a tad more resilient in containers
# - gthread works well for Flask + light I/O
# - --timeout 30 avoids premature kills on cold starts
# - --worker-tmp-dir /dev/shm avoids tmpfs issues on some hosts
[cite_start]CMD ["gunicorn", "-w", "2", "-k", "gthread", "--threads", "4", "--timeout", "30", "--worker-tmp-dir", "/dev/shm", "--log-level", "info", "-b", "0.0.0.0:5001", "nova:app"] [cite: 2]