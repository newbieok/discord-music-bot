# Dockerfile - Discord Bot + voice + yt-dlp
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Cài system dependencies cần thiết cho voice + ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    libopus-dev \
    libsodium23 \
    libsodium-dev \
    build-essential \
    python3-dev \
    git \
 && rm -rf /var/lib/apt/lists/*

# Copy file source code và requirements
COPY . /app

# Upgrade pip và cài Python packages
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Chạy bot
CMD ["python", "bot.py"]
