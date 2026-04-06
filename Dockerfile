# Dockerfile
FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Copy code
COPY . /app

# Install ffmpeg & dependencies
RUN apt-get update && apt-get install -y ffmpeg libopus0 && rm -rf /var/lib/apt/lists/*

# Upgrade pip & install python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Run bot
CMD ["python", "bot.py"]
