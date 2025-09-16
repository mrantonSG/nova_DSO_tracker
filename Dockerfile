# Use an official Python runtime as a parent image
# Using python:3.11 based on your previous logs. Use slim for smaller size.
FROM python:3.11-slim

# Set environment variables to prevent Python from writing pyc files and buffering stdout/stderr
# Using newer ENV KEY=VALUE format
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that might be needed by Python packages (e.g., for matplotlib backends)
# Add more if needed based on build errors
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Example: tk might be needed for some matplotlib backends if Agg fails later
    # tk \
    # Example: build-essential might be needed if some packages need compiling
    # build-essential \
    # Example: git if you needed to install directly from git repos
    # git \
    # Clean up apt cache to reduce image size
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# Use --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# *** ADDED: Upgrade pip and setuptools to mitigate vulnerabilities ***
RUN pip install --no-cache-dir --upgrade pip setuptools

# Copy the rest of the application code into the container
COPY . .

# Make port 5001 available to the world outside this container
EXPOSE 5001

# Define the command to run your app using Gunicorn
# Assumes your Flask app instance is named 'app' in 'nova.py'
# Runs Gunicorn listening on all interfaces inside the container on port 5001
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--threads", "4", "--log-level", "error", "nova:app"]


