ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-aiohttp

# EmulatorJS (GPL-3.0) mit mGBA-Kern, fest auf eine Version gepinnt und per SHA-256 geprüft.
# Das npm-Paket enthält nur die einzelnen Skripte; loader.js erwartet emulator.min.js/.css,
# darum werden sie hier in derselben Reihenfolge zusammengefügt, wie der Loader sie lädt.
# Korrektur Steuerkreuz: Die Mitte wird aus der angezeigten Größe berechnet (rect statt
# clientWidth), sonst trifft es daneben, sobald die Touch-Steuerung vergrößert ist.
ARG EJS_VERSION=4.2.3
ARG EJS_SHA256=8033a18a3398cae5efd7a4c12a633b10672435977290959633d671365b589639
ARG MGBA_SHA256=9ea664b4f998f6304a49c31a427206e2c12a3c1aa3652765ec5aa6db42cad529
RUN set -eux; \
    cd /tmp; \
    wget -q -O ejs.tgz "https://registry.npmjs.org/@emulatorjs/emulatorjs/-/emulatorjs-${EJS_VERSION}.tgz"; \
    wget -q -O mgba.tgz "https://registry.npmjs.org/@emulatorjs/core-mgba/-/core-mgba-${EJS_VERSION}.tgz"; \
    echo "${EJS_SHA256}  ejs.tgz" | sha256sum -c -; \
    echo "${MGBA_SHA256}  mgba.tgz" | sha256sum -c -; \
    mkdir -p ejs mgba /opt/emulatorjs; \
    tar -xzf ejs.tgz -C ejs; \
    tar -xzf mgba.tgz -C mgba; \
    mv ejs/package/data /opt/emulatorjs/data; \
    cp ejs/package/LICENSE /opt/emulatorjs/LICENSE; \
    D=/opt/emulatorjs/data; \
    mkdir -p "$D/cores/reports"; \
    cp mgba/package/*.data "$D/cores/"; \
    cp mgba/package/reports/mgba.json "$D/cores/reports/"; \
    for f in emulator.js nipplejs.js shaders.js storage.js gamepad.js GameManager.js socket.io.min.js compression.js; do \
        cat "$D/src/$f"; printf '\n;\n'; \
    done > "$D/emulator.min.js"; \
    cp "$D/emulator.css" "$D/emulator.min.css"; \
    sed -i 's|rect.left - dpadMain.clientWidth / 2|rect.left - rect.width / 2|; s|rect.top - dpadMain.clientHeight / 2|rect.top - rect.height / 2|' "$D/emulator.min.js"; \
    grep -q 'touch.clientX - rect.left - rect.width / 2' "$D/emulator.min.js"; \
    grep -q 'touch.clientY - rect.top - rect.height / 2' "$D/emulator.min.js"; \
    if grep -q 'dpadMain.clientWidth / 2' "$D/emulator.min.js"; then echo "Steuerkreuz-Korrektur fehlgeschlagen"; exit 1; fi; \
    rm -rf /tmp/ejs /tmp/mgba /tmp/*.tgz

WORKDIR /app
COPY server.py /app/
COPY web /app/web

CMD ["python3", "-u", "/app/server.py"]
