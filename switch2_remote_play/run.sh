#!/usr/bin/env bashio
# Startet den Ingress-Server. Die Optionen liest server.py selbst aus /data/options.json;
# hier werden sie nur zur Kontrolle ins Protokoll geschrieben.

bashio::log.info "Game Remote startet"
bashio::log.info "Raspberry Pi 5 Bridge: $(bashio::config 'pi_host') (WebSocket $(bashio::config 'pi_ws_port'), Video $(bashio::config 'pi_video_port'))"
bashio::log.info "Stream-Modus: $(bashio::config 'stream_mode')"

exec python3 /app/server.py
