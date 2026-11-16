# Offizielles Ollama-Image (Ubuntu-basiert, arm64 und amd64). BUILD_FROM wird hier bewusst nicht benutzt.
FROM ollama/ollama:0.40.13

RUN apt-get update && apt-get install -y --no-install-recommends jq curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENTRYPOINT []
CMD ["/run.sh"]
