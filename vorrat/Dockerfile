ARG BUILD_FROM
FROM ${BUILD_FROM}

# s6-overlay (/init im Basis-Image) leert sonst die Umgebung für CMD: SUPERVISOR_TOKEN und die
# eigenen ENV-Werte kämen im Programm nicht an.
ENV S6_KEEP_ENV=1

RUN apk add --no-cache python3 py3-aiohttp py3-paho-mqtt py3-pillow

ARG BUILD_VERSION
ENV VORRAT_VERSION=${BUILD_VERSION}

WORKDIR /app
COPY app /app

CMD ["python3", "-u", "/app/server.py"]
