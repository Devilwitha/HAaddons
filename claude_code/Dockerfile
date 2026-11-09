ARG BUILD_FROM
FROM ${BUILD_FROM}

# Laufzeit-Abhängigkeiten (Claude Code native Build auf Alpine/musl braucht libgcc, libstdc++, ripgrep)
RUN apk add --no-cache \
        bash curl git jq yq ripgrep tmux ttyd nano less \
        openssh-client python3 libgcc libstdc++ ca-certificates

ENV USE_BUILTIN_RIPGREP=0 \
    DISABLE_AUTOUPDATER=1

# Claude Code (nativer Installer), Binary nach /usr/local/bin kopieren,
# damit HOME zur Laufzeit auf /data (persistent) zeigen kann.
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp -L /root/.local/bin/claude /usr/local/bin/claude \
    && chmod +x /usr/local/bin/claude \
    && rm -rf /root/.local /root/.claude /root/.claude.json \
    && claude --version

COPY rootfs/ /
RUN chmod +x /usr/local/bin/* /run.sh

CMD [ "/run.sh" ]
