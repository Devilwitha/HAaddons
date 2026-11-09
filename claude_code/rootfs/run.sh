#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

# Persistentes HOME: Login, Settings, Verlauf, SSH-Keys überleben Updates/Neustarts
export HOME=/data/home
export TERM=xterm-256color
mkdir -p "${HOME}/.claude"

# Token für Kindprozesse (tmux, Remote-Control-Sessions) bereitstellen
export SUPERVISOR_TOKEN

# ---- Optionen ---------------------------------------------------------------
MODEL="$(bashio::config 'model' '')"
if [ -n "${MODEL}" ]; then
    export ANTHROPIC_MODEL="${MODEL}"
fi
PERMISSION_MODE="$(bashio::config 'permission_mode')"
SESSION_NAME="$(bashio::config 'session_name')"
export RC_SESSION_NAME="${SESSION_NAME}"

# ---- settings.json: nur defaultMode setzen, Rest des Users beibehalten -------
SETTINGS="${HOME}/.claude/settings.json"
[ -s "${SETTINGS}" ] || echo '{}' > "${SETTINGS}"
tmp="$(mktemp)"
jq --arg m "${PERMISSION_MODE}" '.permissions.defaultMode = $m' "${SETTINGS}" > "${tmp}" && mv "${tmp}" "${SETTINGS}"

# ---- /config als vertrauenswürdigen Workspace markieren ---------------------
# (Remote Control startet nur in einem Ordner, dessen Trust-Dialog akzeptiert wurde)
STATE="${HOME}/.claude.json"
[ -s "${STATE}" ] || echo '{}' > "${STATE}"
tmp="$(mktemp)"
jq '.projects["/config"].hasTrustDialogAccepted = true' "${STATE}" > "${tmp}" && mv "${tmp}" "${STATE}"

# ---- HAOS-Kontext für Claude (wird bei jedem Start neu geschrieben) ----------
cp /usr/share/claude-haos/haos-addon.md "${HOME}/.claude/haos-addon.md"
USER_MD="${HOME}/.claude/CLAUDE.md"
if [ ! -f "${USER_MD}" ]; then
    printf '# Persönliche Anweisungen\n\n@~/.claude/haos-addon.md\n' > "${USER_MD}"
elif ! grep -q 'haos-addon.md' "${USER_MD}"; then
    printf '\n@~/.claude/haos-addon.md\n' >> "${USER_MD}"
fi

# ---- tmux --------------------------------------------------------------------
if [ ! -f "${HOME}/.tmux.conf" ]; then
    cat > "${HOME}/.tmux.conf" <<'EOF'
set -g mouse on
set -g history-limit 20000
set -g default-terminal "tmux-256color"
EOF
fi

cd /config
tmux kill-server 2>/dev/null || true
tmux new-session -d -s main -n shell -c /config
tmux send-keys -t main:shell "clear; cat /usr/share/claude-haos/motd" Enter

if bashio::config.true 'remote_control'; then
    bashio::log.info "Starte Claude Remote Control (Session: ${SESSION_NAME})"
    tmux new-window -d -t main -n remote -c /config rc-loop
else
    bashio::log.info "Remote Control deaktiviert"
fi

bashio::log.info "Web-Terminal läuft auf Port 7681 (Ingress)"
exec ttyd -W -p 7681 -t titleFixed="Claude Code" -t fontSize=14 \
    tmux new-session -A -s main
