ARG BUILD_FROM
FROM ${BUILD_FROM}

# Laufzeit-Abhängigkeiten (Claude Code native Build auf Alpine/musl braucht libgcc, libstdc++, ripgrep)
RUN apk add --no-cache \
        bash curl git jq ripgrep nano less \
        openssh-client-default python3 py3-aiohttp \
        libgcc libstdc++ ca-certificates

ENV USE_BUILTIN_RIPGREP=0 \
    DISABLE_AUTOUPDATER=1 \
    CC_CWD=/config \
    CC_PORT=8099

# Claude Code (nativer Installer), Binary nach /usr/local/bin kopieren,
# damit HOME zur Laufzeit auf /data (persistent) zeigen kann.
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp -L /root/.local/bin/claude /usr/local/bin/claude \
    && chmod +x /usr/local/bin/claude \
    && rm -rf /root/.local /root/.claude /root/.claude.json \
    && claude --version

COPY rootfs/ /
RUN chmod +x /usr/local/bin/ha-* /run.sh

CMD [ "/run.sh" ]
