# --- Stage 1: Build Environment ---
# This stage installs Google Chrome and the correct chromedriver
FROM python:3.10-slim AS builder

# Install necessary system dependencies for Chrome and chromedriver
# Added 'curl' and 'gpg' which are needed for the new key management method
RUN apt-get update && apt-get install -y wget gnupg unzip curl gpg

# --- NEW, MODERN WAY TO ADD GOOGLE'S KEY ---
# Download the key and save it to the trusted keyring directory
RUN curl -sS https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-archive-keyring.gpg
# Add the Google Chrome repository, referencing the new key file
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-archive-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
# --- END NEW METHOD ---

# Install Google Chrome Stable
RUN apt-get update && apt-get install -y google-chrome-stable

# Find the Chrome version and install the matching chromedriver for it
# This robust method ensures they are always compatible
RUN CHROME_VERSION=$(google-chrome --version | cut -f 3 -d ' ' | cut -d '.' -f 1-3) \
    && DRIVER_VERSION=$(wget -qO- "https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build.json" | grep -oP "\"${CHROME_VERSION}\":{\"version\":\"\K[^\"]+") \
    && wget -q "https://storage.googleapis.com/chrome-for-testing-public/${DRIVER_VERSION}/linux64/chromedriver-linux64.zip" \
    && unzip chromedriver-linux64.zip \
    && mv chromedriver-linux64/chromedriver /usr/local/bin/ \
    && rm chromedriver-linux64.zip

# --- Stage 2: Final Application ---
# This stage builds the clean, final image for your app
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy Chrome browser and chromedriver from the builder stage
COPY --from=builder /opt/google/chrome /opt/google/chrome
COPY --from=builder /usr/local/bin/chromedriver /usr/local/bin/chromedriver

# Add Chrome to the system's PATH
ENV PATH="/opt/google/chrome:${PATH}"

# Copy the application files into the container
COPY requirements.txt .
COPY main.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Tell Docker that the container will listen on port 8000
EXPOSE 8000

# Set the final command to run your application using Gunicorn
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "main:app"]