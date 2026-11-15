ARG BUILD_FROM
FROM ${BUILD_FROM}

# aiohttp für den Ingress-Server. Auf armv7 gibt es nicht für jedes Paket ein fertiges
# musl-Wheel; dann wird ohne C-Erweiterungen gebaut (reines Python, gleiche Funktion).
ENV AIOHTTP_NO_EXTENSIONS=1 \
    MULTIDICT_NO_EXTENSIONS=1 \
    YARL_NO_EXTENSIONS=1 \
    FROZENLIST_NO_EXTENSIONS=1 \
    PROPCACHE_NO_EXTENSIONS=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apk add --no-cache --virtual .build-deps gcc musl-dev \
    && pip install "aiohttp==3.12.15" "paho-mqtt==2.1.0" \
    && apk del .build-deps

ARG BUILD_VERSION
ENV SWITCH2_VERSION=${BUILD_VERSION}

COPY run.sh /run.sh
COPY app /app
RUN chmod a+x /run.sh

CMD ["/run.sh"]
