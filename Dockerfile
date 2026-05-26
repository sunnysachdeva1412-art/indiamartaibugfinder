FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app

# Install Python deps first (layer cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Chromium is installed inside this image
RUN playwright install chromium

# Copy app source
COPY . .

# Create output dir (persists reports during the container's lifetime)
RUN mkdir -p web_output

ENV PYTHONUNBUFFERED=1
# ANTHROPIC_API_KEY must be injected at runtime via platform env vars — never bake it in here

EXPOSE 8080

# Gunicorn: 1 worker (Playwright is not fork-safe), long timeout for crawls
CMD ["gunicorn", "server:app", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "300"]
