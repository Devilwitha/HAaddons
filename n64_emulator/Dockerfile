ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-aiohttp py3-paho-mqtt

# EmulatorJS (GPL-3.0) mit den N64-Kernen mupen64plus_next (Standard) und parallel_n64
# (nimmt EmulatorJS auf iPhone/iPad), fest auf eine Version gepinnt und per SHA-256 geprüft.
# Das npm-Paket enthält nur die einzelnen Skripte; loader.js erwartet emulator.min.js/.css,
# darum werden sie hier in derselben Reihenfolge zusammengefügt, wie der Loader sie lädt.
# Korrektur Steuerkreuz: Die Mitte wird aus der angezeigten Größe berechnet (rect statt
# clientWidth), sonst trifft es daneben, sobald die Touch-Steuerung vergrößert ist.
# Korrektur Analogstick: Bei genau 0° (waagerecht nach rechts) kam kein Ausschlag an.
ARG EJS_VERSION=4.2.3
ARG EJS_SHA256=8033a18a3398cae5efd7a4c12a633b10672435977290959633d671365b589639
ARG MUPEN_SHA256=2b8952aabbe32dc620cbde31b4456a16d7ff96116f471283699dc9ae09724417
ARG PARALLEL_SHA256=b83cb21ac25b84db646737119083836607f37b0b94833d40669d8d72cca81d5a
RUN set -eux; \
    cd /tmp; \
    wget -q -O ejs.tgz "https://registry.npmjs.org/@emulatorjs/emulatorjs/-/emulatorjs-${EJS_VERSION}.tgz"; \
    wget -q -O mupen.tgz "https://registry.npmjs.org/@emulatorjs/core-mupen64plus_next/-/core-mupen64plus_next-${EJS_VERSION}.tgz"; \
    wget -q -O parallel.tgz "https://registry.npmjs.org/@emulatorjs/core-parallel_n64/-/core-parallel_n64-${EJS_VERSION}.tgz"; \
    echo "${EJS_SHA256}  ejs.tgz" | sha256sum -c -; \
    echo "${MUPEN_SHA256}  mupen.tgz" | sha256sum -c -; \
    echo "${PARALLEL_SHA256}  parallel.tgz" | sha256sum -c -; \
    mkdir -p ejs mupen parallel /opt/emulatorjs; \
    tar -xzf ejs.tgz -C ejs; \
    tar -xzf mupen.tgz -C mupen; \
    tar -xzf parallel.tgz -C parallel; \
    mv ejs/package/data /opt/emulatorjs/data; \
    cp ejs/package/LICENSE /opt/emulatorjs/LICENSE; \
    D=/opt/emulatorjs/data; \
    mkdir -p "$D/cores/reports"; \
    cp mupen/package/*.data parallel/package/*.data "$D/cores/"; \
    cp mupen/package/reports/*.json parallel/package/reports/*.json "$D/cores/reports/"; \
    for f in emulator.js nipplejs.js shaders.js storage.js gamepad.js GameManager.js socket.io.min.js compression.js; do \
        cat "$D/src/$f"; printf '\n;\n'; \
    done > "$D/emulator.min.js"; \
    cp "$D/emulator.css" "$D/emulator.min.css"; \
    sed -i 's|rect.left - dpadMain.clientWidth / 2|rect.left - rect.width / 2|; s|rect.top - dpadMain.clientHeight / 2|rect.top - rect.height / 2|' "$D/emulator.min.js"; \
    grep -q 'touch.clientX - rect.left - rect.width / 2' "$D/emulator.min.js"; \
    grep -q 'touch.clientY - rect.top - rect.height / 2' "$D/emulator.min.js"; \
    if grep -q 'dpadMain.clientWidth / 2' "$D/emulator.min.js"; then echo "Steuerkreuz-Korrektur fehlgeschlagen"; exit 1; fi; \
    sed -i 's|if (degree > 0 && degree <= 45) {|if (degree >= 0 \&\& degree <= 45) {|; s|if (degree > 315 && degree <= 359.9) {|if (degree > 315 \&\& degree <= 360) {|' "$D/emulator.min.js"; \
    grep -q 'if (degree >= 0 && degree <= 45) {' "$D/emulator.min.js"; \
    grep -q 'if (degree > 315 && degree <= 360) {' "$D/emulator.min.js"; \
    rm -rf /tmp/ejs /tmp/mupen /tmp/parallel /tmp/*.tgz

ARG BUILD_VERSION
ENV EMU_SYSTEM=n64 \
    EMU_VERSION=${BUILD_VERSION}

WORKDIR /app
COPY server.py ha_sensors.py /app/
COPY web /app/web

CMD ["python3", "-u", "/app/server.py"]
