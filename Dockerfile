FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Copy files
COPY . /app

# Upgrade pip & install deps
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Create temp folder for music
RUN mkdir -p /tmp/music

# Run bot
CMD ["python", "bot.py"]
