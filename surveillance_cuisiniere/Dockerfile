FROM debian:bookworm-slim

# OpenCV (avec FFmpeg pour lire le RTSP), NumPy et le client MQTT, depuis les paquets Debian.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-opencv python3-numpy python3-paho-mqtt \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY run.py detection.py index.html /app/

CMD ["python3", "-u", "/app/run.py"]
