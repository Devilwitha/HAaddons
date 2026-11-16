#!/usr/bin/env bash
# Startet Ollama, Modelle liegen dauerhaft in /data. Fehlende Modelle aus den Optionen werden geladen.
set -euo pipefail

export OLLAMA_HOST=0.0.0.0:11434
export OLLAMA_MODELS=/data/models
export OLLAMA_KEEP_ALIVE="$(jq -r '.keep_alive // "5m"' /data/options.json)"
# Auf dem Pi nur ein Modell und eine Anfrage gleichzeitig, sonst wird der RAM knapp
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
mkdir -p "$OLLAMA_MODELS"

ollama serve &
server=$!

for _ in $(seq 1 60); do
    curl -sf http://127.0.0.1:11434/api/version >/dev/null && break
    sleep 1
done

for model in $(jq -r '.models[]' /data/options.json); do
    if ollama list | awk 'NR>1 {print $1}' | grep -qx "$model"; then
        echo "Modell $model vorhanden"
    else
        echo "Lade Modell $model (einmalig, kann dauern) ..."
        if ollama pull "$model" >/tmp/pull.log 2>&1; then echo "Modell $model geladen"; else tail -3 /tmp/pull.log; echo "WARNUNG: $model konnte nicht geladen werden"; fi
    fi
done

echo "Ollama bereit: http://045e59a3-ollama:11434"
wait "$server"
