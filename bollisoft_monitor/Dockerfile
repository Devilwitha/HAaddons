ARG BUILD_FROM
FROM ${BUILD_FROM}

# s6-overlay (/init im Basis-Image) leert sonst die Umgebung für CMD: SUPERVISOR_TOKEN und die
# eigenen ENV-Werte (System, Version) kämen im Programm nicht an.
ENV S6_KEEP_ENV=1

RUN apk add --no-cache python3 py3-aiohttp py3-paho-mqtt

WORKDIR /app
COPY monitor.py /app/
COPY web /app/web

CMD ["python3", "-u", "/app/monitor.py"]
