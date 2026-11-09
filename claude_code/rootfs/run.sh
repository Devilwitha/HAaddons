#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

# Persistentes HOME: Login, Einstellungen und Chat-Verlauf überleben Updates/Neustarts
export HOME=/data/home
mkdir -p "${HOME}/.claude"

# Token für Kindprozesse (Claude, Remote Control) bereitstellen
export SUPERVISOR_TOKEN

# /config als vertrauenswürdigen Arbeitsordner markieren
STATE="${HOME}/.claude.json"
[ -s "${STATE}" ] || echo '{}' > "${STATE}"
tmp="$(mktemp)"
jq '.projects["/config"].hasTrustDialogAccepted = true' "${STATE}" > "${tmp}" && mv "${tmp}" "${STATE}"

# HAOS-Kontext für Claude (wird bei jedem Start aktualisiert)
cp /usr/share/claude-haos/haos-addon.md "${HOME}/.claude/haos-addon.md"
USER_MD="${HOME}/.claude/CLAUDE.md"
if [ ! -f "${USER_MD}" ]; then
    printf '# Persönliche Anweisungen\n\n@~/.claude/haos-addon.md\n' > "${USER_MD}"
elif ! grep -q 'haos-addon.md' "${USER_MD}"; then
    printf '\n@~/.claude/haos-addon.md\n' >> "${USER_MD}"
fi

bashio::log.info "Starte Claude-Oberfläche"
cd /config
exec python3 /app/server.py
