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

FROM ghcr.io/getpaseo/paseo:0.17.4@sha256:58c4a16ba787a3e77b84fa5d82250ecf143c23fc8a869ca701bb17ce7c92a6ce

RUN set -eux; \
    cd /tmp; \
    curl -fLsS --retry 3 -o opencode.tgz https://github.com/anomalyco/opencode/releases/download/v2.3.2/opencode-linux-x64.tar.gz; \
    curl -fLsS --retry 3 -o codex.tgz https://github.com/openai/codex/releases/download/rust-v0.171.0/codex-x86_64-unknown-linux-musl.tar.gz; \
    echo '563b5d663637226b6e3bb4bb6803281bc39b0d2cd88db06d674b6420aab009b0  opencode.tgz' | sha256sum -c -; \
    echo '01baa2e47809f8597be5b2f8abd4e4dfe3fb5bcb6e504c2fbaaad97056c15e8d  codex.tgz' | sha256sum -c -; \
    tar xzf opencode.tgz --no-same-owner -C /usr/local/bin opencode; \
    tar xzf codex.tgz --no-same-owner -C /usr/local/bin codex-x86_64-unknown-linux-musl; \
    mv /usr/local/bin/codex-x86_64-unknown-linux-musl /usr/local/bin/codex; \
    rm opencode.tgz codex.tgz; \
    groupadd --gid 12074 homecompute-paseo; \
    useradd --uid 12074 --gid 12074 --home-dir /home/paseo --no-create-home --shell /bin/bash homecompute-paseo

COPY --from=hermes /opt/hermes /opt/hermes
RUN ln -s /opt/hermes/python-runtime/bin/python3 /usr/local/bin/python3
COPY --chmod=0555 hermes-code-review.py /usr/local/bin/hermes-code-review
COPY --chmod=0555 read-github-backlog.py /usr/local/bin/paseo-github-backlog
COPY projects.json register-projects.py /etc/paseo/
USER 12074:12074
