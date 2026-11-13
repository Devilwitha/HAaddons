ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-aiohttp py3-paho-mqtt

WORKDIR /app
COPY monitor.py /app/
COPY web /app/web

CMD ["python3", "-u", "/app/monitor.py"]
