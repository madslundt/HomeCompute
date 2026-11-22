FROM node:24-bookworm@sha256:aca79828f8a4afc627f189387c965625bcc419233946210a3b9522d90a59b5c6 AS hermes

# Use the same pinned Hermes source and Python as the managed HomeCompute release.
ADD --checksum=sha256:0250ce85c071ebceefc5b9d1b58a7a52f71d2849a6ef1d0a70af07d108ef08dc https://codeload.github.com/NousResearch/hermes-agent/tar.gz/2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19 /tmp/hermes.tar.gz
ADD --checksum=sha256:dfa4c5a40c47f338f41658017be550c72247c391e6bfb1fa4da03ebc8451abdf https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.14.9+20261021-x86_64-unknown-linux-gnu-install_only.tar.gz /tmp/python.tar.gz
WORKDIR /opt/hermes
COPY hermes-code-review.patch /tmp/hermes-code-review.patch
RUN mkdir python-runtime \
    && tar -xzf /tmp/hermes.tar.gz --strip-components=1 -C /opt/hermes \
    && tar -xzf /tmp/python.tar.gz --strip-components=1 -C python-runtime \
    && git apply /tmp/hermes-code-review.patch \
    && HERMES_HOME=/tmp/hermes-build python-runtime/bin/python3 -m pm.build_env \
        --source /opt/hermes --python /opt/hermes/python-runtime/bin/python3 \
        --out /opt/hermes/.venv --sealed --extra acp \
    && python-runtime/bin/python3 scripts/write_install_stamp.py \
        --output /opt/hermes/install-stamp.json --commit 2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19 \
        --base-version 0.23.0 --distance 0 --dirty --source homecompute --distribution docker --update-mechanism external \
    && chmod -R a+rX .venv python-runtime \
    && rm -rf tests website .github

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

COPY policy.json codex-local-models.json projects.json /etc/t3code/
COPY --from=hermes /opt/hermes /opt/hermes
COPY --chmod=0555 hermes-code-review.py /usr/local/bin/hermes-code-review
COPY --chmod=0555 read-github-backlog.py /usr/local/bin/t3-github-backlog

ENV HOME=/data T3CODE_HOME=/data/.t3
USER 12073:12073
WORKDIR /data
