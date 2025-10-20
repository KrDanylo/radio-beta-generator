# --- Stage 1: Build Environment ---
# This stage installs Google Chrome and the correct chromedriver
FROM python:3.10-slim AS builder

# Install necessary system dependencies
RUN apt-get update && apt-get install -y wget gnupg unzip curl gpg

# Add Google's key and repository using the modern method
RUN curl -sS https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-archive-keyring.gpg
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-archive-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list

# Install Google Chrome and then clean up temporary files
RUN apt-get update && apt-get install -y google-chrome-stable --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Find and install the matching chromedriver
RUN CHROME_VERSION=$(google-chrome --version | cut -f 3 -d ' ' | cut -d '.' -f 1-3) \
    && DRIVER_VERSION=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build.json" | grep -oP "\"${CHROME_VERSION}\":{\"version\":\"\K[^\"]+") \
    && wget -q "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip" \
    && unzip chromedriver-linux64.zip \
    && mv chromedriver-linux64/chromedriver /usr/local/bin/ \
    && rm chromedriver-linux64.zip

# --- Stage 2: Final Application ---
FROM python:3.10-slim

WORKDIR /app

# Copy assets from the builder stage
COPY --from=builder /opt/google/chrome /opt/google/chrome
COPY --from=builder /usr/local/bin/chromedriver /usr/local/bin/chromedriver
ENV PATH="/opt/google/chrome:${PATH}"

# Copy application files
COPY requirements.txt .
COPY main.py .

# Install Python dependencies and then clean up the pip cache
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "main:app"]