ARG BUILD_FROM
FROM $BUILD_FROM

# numpy/Pillow aus Alpine (fertig gebaut), der Rest per pip
RUN apk add --no-cache python3 py3-pip py3-numpy py3-pillow py3-yaml py3-requests \
    && pip install --no-cache-dir --break-system-packages "paho-mqtt>=2.0,<3" \
    && (pip install --no-cache-dir --break-system-packages "anthropic>=2.3.0" \
        || echo "anthropic nicht installiert – Bewertung 'claude' steht dann nicht zur Verfügung")

# Programmcode (pi/werwardas) aus dem Repo holen. Beim Bauen des Add-ons ist nur dieser Ordner verfügbar.
ARG SOURCE_URL=https://github.com/Devilwitha/werwardas/archive/refs/heads/main.tar.gz
ADD $SOURCE_URL /tmp/source.tar.gz
RUN mkdir -p /tmp/source /app \
    && tar -xzf /tmp/source.tar.gz -C /tmp/source --strip-components=1 \
    && cp -r /tmp/source/pi/werwardas /app/werwardas \
    && rm -rf /tmp/source /tmp/source.tar.gz

COPY run.sh make_config.py /
RUN chmod a+x /run.sh

WORKDIR /app
CMD ["/run.sh"]
