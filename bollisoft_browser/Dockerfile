ARG BUILD_FROM
FROM ${BUILD_FROM}

# s6-overlay (/init im Basis-Image) leert sonst die Umgebung für CMD: SUPERVISOR_TOKEN und die
# eigenen ENV-Werte kämen im Programm nicht an.
ENV S6_KEEP_ENV=1

# Chromium läuft als eigener Benutzer ohne Root-Rechte und ohne SUPERVISOR_TOKEN in der Umgebung
RUN apk add --no-cache python3 py3-aiohttp chromium tzdata font-dejavu font-liberation font-noto-emoji \
  && adduser -D -h /home/browser -s /sbin/nologin browser \
  && fc-cache -f \
  && (command -v chromium-browser || command -v chromium)

ARG BUILD_VERSION
ENV BROWSER_VERSION=${BUILD_VERSION}

WORKDIR /app
COPY app /app

CMD ["python3", "-u", "/app/server.py"]
