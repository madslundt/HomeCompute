FROM node:24-bookworm@sha256:aca79828f8a4afc627f189387c965625bcc419233946210a3b9522d90a59b5c6

RUN set -eux; \
    cd /tmp; \
    curl -fLsS --retry 3 -o orca.tgz https://github.com/stablyai/orca/releases/download/v1.4.268/orca-server-linux-x64.tar.gz; \
    curl -fLsS --retry 3 -o orca-web.deb https://github.com/stablyai/orca/releases/download/v1.4.268/orca-ide_1.4.268_amd64.deb; \
    curl -fLsS --retry 3 -o opencode.tgz https://github.com/anomalyco/opencode/releases/download/v2.3.2/opencode-linux-x64.tar.gz; \
    curl -fLsS --retry 3 -o codex.tgz https://github.com/openai/codex/releases/download/rust-v0.171.0/codex-x86_64-unknown-linux-musl.tar.gz; \
    echo '7e84701fb7e034de8d37050c7f96b6b36289fd0f777cbed4be471e6e2130a93f  orca.tgz' | sha256sum -c -; \
    echo 'ce08aa4bb05972ee3acc89f6c97ce6140c84014a14b0c7e70fe6ece1b40b28a7  orca-web.deb' | sha256sum -c -; \
    echo '563b5d663637226b6e3bb4bb6803281bc39b0d2cd88db06d674b6420aab009b0  opencode.tgz' | sha256sum -c -; \
    echo '01baa2e47809f8597be5b2f8abd4e4dfe3fb5bcb6e504c2fbaaad97056c15e8d  codex.tgz' | sha256sum -c -; \
    mkdir /opt/orca; \
    tar xzf orca.tgz --no-same-owner --strip-components=1 -C /opt/orca; \
    mkdir -p /opt/orca/out renderer-extract; \
    dpkg-deb --fsys-tarfile orca-web.deb | tar x --no-same-owner -C renderer-extract ./opt/Orca/resources/app.asar.unpacked/out/renderer; \
    mv renderer-extract/opt/Orca/resources/app.asar.unpacked/out/renderer /opt/orca/out/renderer; \
    test -s /opt/orca/out/renderer/web-index.html; \
    test -s /opt/orca/out/renderer/.orca-web-client-files.json; \
    tar xzf opencode.tgz --no-same-owner -C /usr/local/bin opencode; \
    tar xzf codex.tgz --no-same-owner -C /usr/local/bin codex-x86_64-unknown-linux-musl; \
    ln -s /usr/local/bin/opencode /usr/local/bin/opencode2; \
    mv /usr/local/bin/codex-x86_64-unknown-linux-musl /usr/local/bin/codex; \
    ln -s /opt/orca/bin/orca-ide /usr/local/bin/orca; \
    ln -s /opt/orca/bin/orca-ide /usr/local/bin/orca-ide; \
    groupadd --gid 12074 orca; \
    useradd --uid 12074 --gid 12074 --home-dir /data --no-create-home --shell /bin/bash orca; \
    rm -r orca.tgz orca-web.deb opencode.tgz codex.tgz renderer-extract

COPY opencode.json /etc/homecompute-orca/opencode.json
COPY teams.json /etc/homecompute-orca/teams.json
COPY instructions /etc/homecompute-orca/instructions
COPY project-status.py /usr/local/bin/orca-project-status
COPY schedules.py /usr/local/bin/orca-reconcile-schedules

ENV HOME=/data ORCA_USER_DATA_PATH=/data/profile \
    ORCA_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 \
    OPENCODE_CONFIG=/etc/homecompute-orca/opencode.json \
    OPENCODE_DISABLE_PROJECT_CONFIG=true OPENCODE_DISABLE_MODELS_FETCH=true \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=true GIT_TERMINAL_PROMPT=0

USER 12074:12074
WORKDIR /data
CMD ["orca", "serve", "--bind", "0.0.0.0", "--port", "6768", "--pairing-address", "wss://orca.home.arpa", "--no-pairing", "--refuse-public", "--supervise"]
