FROM node:24-bookworm@sha256:aca79828f8a4afc627f189387c965625bcc419233946210a3b9522d90a59b5c6

# Codex's nested sandbox can re-execute the CLI from /usr/local.
RUN set -eux; \
    t3_version=0.1.11; \
    codex_version=0.169.0; \
    mkdir /opt/t3code /usr/local/lib/codex; \
    cd /tmp; \
    curl -fLsS --retry 3 -o t3.tgz "https://github.com/pingdotgg/t3code/releases/download/v${t3_version}/t3-${t3_version}-linux-x64.tar.gz"; \
    echo '2b691c07f5bcb5adea4a863d5324aae7db21bd6d82280cec7787836dcaa013e1  t3.tgz' | sha256sum -c -; \
    tar -xzf t3.tgz --no-same-owner --strip-components=1 -C /opt/t3code; \
    curl -fLsS --retry 3 -o codex.tgz "https://registry.npmjs.org/@openai/codex/-/codex-${codex_version}-linux-x64.tgz"; \
    echo '89801b4b97faba562eb80fe0a833159ac79399be674022f4b1a0ccab1d153faa  codex.tgz' | sha256sum -c -; \
    tar -xzf codex.tgz --no-same-owner --strip-components=3 -C /usr/local/lib/codex \
        package/vendor/x86_64-unknown-linux-musl; \
    rm t3.tgz codex.tgz; \
    ln -s /opt/t3code/t3 /usr/local/bin/t3; \
    ln -s /usr/local/lib/codex/bin/codex /usr/local/bin/codex; \
    groupadd --gid 12073 t3code; \
    useradd --uid 12073 --gid 12073 --home-dir /data --no-create-home --shell /bin/bash t3code

COPY policy.json /etc/t3code/policy.json

ENV HOME=/data T3CODE_HOME=/data/.t3
USER 12073:12073
WORKDIR /data
