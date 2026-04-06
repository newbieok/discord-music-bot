# Dockerfile - Bot Discord + voice + yt-dlp
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy files
COPY . /app

# Upgrade pip và cài dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Chạy bot
CMD ["python", "bot.py"]
