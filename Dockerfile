# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

#
# --- THIS IS THE UPDATED SECTION ---
#
# Install system dependencies. `build-essential` and `python3-dev` are crucial
# for compiling Python packages from source on different CPU architectures.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and setuptools first for better layer caching
RUN pip install --no-cache-dir --upgrade pip setuptools

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Make port 5001 available to the world outside this container
EXPOSE 5001

# Define the command to run your app using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--threads", "4", "--log-level", "error", "nova:app"]