# Qualification only. Production stays on ../Dockerfile until all gates pass.
# Retain the complete OpenShell/NemoClaw runtime, identities and supervisor.
FROM ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:f52d55af05b63302ca8ae54ca9141b80df433a72ce273ae4aeac08c5af2cb990
USER root
ARG NEMOCLAW_CORPORATE_CA_B64=
RUN if [ -n "$NEMOCLAW_CORPORATE_CA_B64" ]; then \
      printf '%s' "$NEMOCLAW_CORPORATE_CA_B64" | base64 --decode > /usr/local/share/nemoclaw/corporate-ca.pem \
      && openssl crl2pkcs7 -nocrl -certfile /usr/local/share/nemoclaw/corporate-ca.pem > /dev/null \
      && cp /usr/local/share/nemoclaw/corporate-ca.pem /usr/local/share/ca-certificates/homecompute.crt \
      && chmod 0444 /usr/local/share/nemoclaw/corporate-ca.pem /usr/local/share/ca-certificates/homecompute.crt \
      && update-ca-certificates; \
    fi
ADD --checksum=sha256:0250ce85c071ebceefc5b9d1b58a7a52f71d2849a6ef1d0a70af07d108ef08dc https://codeload.github.com/NousResearch/hermes-agent/tar.gz/2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19 /tmp/hermes-latest.tar.gz
ADD --checksum=sha256:dfa4c5a40c47f338f41658017be550c72247c391e6bfb1fa4da03ebc8451abdf https://github.com/astral-sh/python-build-standalone/releases/download/20261021/cpython-3.14.9+20261021-x86_64-unknown-linux-gnu-install_only.tar.gz /tmp/hermes-python.tar.gz
COPY latest/managed-integration.patch /tmp/homecompute-managed-integration.patch
RUN mkdir /tmp/hermes-latest /usr/local/lib/homecompute-python \
    && tar -xzf /tmp/hermes-latest.tar.gz --strip-components=1 -C /tmp/hermes-latest \
    && tar -xzf /tmp/hermes-python.tar.gz --strip-components=1 -C /usr/local/lib/homecompute-python \
    && git -C /tmp/hermes-latest apply --check /tmp/homecompute-managed-integration.patch \
    && git -C /tmp/hermes-latest apply /tmp/homecompute-managed-integration.patch \
    && rm -rf /opt/hermes \
    && mv /tmp/hermes-latest /opt/hermes \
    && ln -s /opt/nemoclaw-hermes-plugin /opt/hermes/plugins/nemoclaw \
    && rm /tmp/hermes-latest.tar.gz /tmp/hermes-python.tar.gz /tmp/homecompute-managed-integration.patch
WORKDIR /opt/hermes
# Use the release's package manager and frozen lock, including the one exact
# PyYAML compatibility extra needed by the retained managed plugin.
RUN HERMES_HOME=/tmp/hermes-build /usr/local/lib/homecompute-python/bin/python3 -m pm.build_env \
      --source /opt/hermes --python /usr/local/lib/homecompute-python/bin/python3 \
      --out /opt/hermes/.venv --sealed --record-selection \
      --extra nemoclaw --extra messaging --extra web --extra mcp --extra acp --extra anthropic \
    && chmod -R a+rX /opt/hermes/.venv /usr/local/lib/homecompute-python \
    && rm -rf /tmp/hermes-build /root/.cache/uv
# The new release seals dependency environments and binds source through its
# offline assembler. A raw editable install has no usable source import path.
RUN /usr/local/lib/homecompute-python/bin/python3 -c 'from pathlib import Path; from pm import stage_manager_runtime; from scripts.bundles.payload import seal_pm_runtime; root=Path("/opt/hermes"); python=Path("/usr/local/lib/homecompute-python/bin/python3").resolve(); stage_manager_runtime(python=python, destination=root/"pm-runtime", project=root/"pm"); seal_pm_runtime(root, python)'
RUN /usr/local/lib/homecompute-python/bin/python3 scripts/write_install_stamp.py \
      --output /opt/hermes/install-stamp.json --commit 2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19 \
      --base-version 0.23.0 --distance 0 --dirty --source homecompute --distribution docker --update-mechanism external
RUN node scripts/build/node-deps.mjs --source /opt/hermes --workspace ui-tui --workspace web \
    && /opt/hermes/.venv/bin/python scripts/generate_icons.py --source /opt/hermes --out /tmp/hermes-icons \
    && node scripts/build/tui.mjs --source /opt/hermes --out /opt/products/tui \
    && node scripts/build/web.mjs --source /opt/hermes --icons /tmp/hermes-icons --out /opt/products/web \
    && cp -a /opt/products/tui/. /opt/hermes/ui-tui/ \
    && mkdir -p /opt/hermes/hermes_cli/web_dist \
    && cp -a /opt/products/web/. /opt/hermes/hermes_cli/web_dist/ \
    && /opt/hermes/.venv/bin/python -m docker.build_agent \
    && chmod -R a+rX /opt/hermes \
    && rm -rf /opt/products /tmp/hermes-icons /root/.npm /root/.cache/uv
# Validate the retained managed CLI adapter against the real new parser. A
# version string alone does not establish compatibility.
RUN python3 -c 'import json; p="/usr/local/share/nemoclaw/hermes-cli-adapter-v1.json"; d=json.load(open(p)); assert d["upstream_cli_version"] == "0.21.3"; d["upstream_cli_version"]="0.23.0"; open(p,"w").write(json.dumps(d)+"\n")' \
    && /opt/hermes/.venv/bin/python -I /usr/local/lib/nemoclaw/validate-hermes-cli-adapter.py \
      --contract /usr/local/share/nemoclaw/hermes-cli-adapter-v1.json --hermes /opt/hermes/.venv/bin/hermes \
    && /usr/local/bin/hermes --version
COPY latest/probe_runtime.py /usr/local/lib/nemoclaw/homecompute-hermes-latest-probe.py
RUN /opt/hermes/.venv/bin/python -I /usr/local/lib/nemoclaw/homecompute-hermes-latest-probe.py
COPY browser-relay.mjs /usr/local/lib/nemoclaw/browser-relay.mjs
RUN chmod 0444 /usr/local/lib/nemoclaw/browser-relay.mjs
ENV HERMES_DUMP_REQUESTS=0 HERMES_DUMP_REQUEST_STDOUT=0 \
    NEMOCLAW_CONTEXT_WINDOW=65536 \
    NEMOCLAW_INFERENCE_PROVIDER_ID=compatible-endpoint \
    NEMOCLAW_MODEL=automation-moe \
    NEMOCLAW_INFERENCE_BASE_URL=http://ai.home.arpa:18080/v1 \
    NEMOCLAW_PROVIDER_KEY=compatible-endpoint \
    NEMOCLAW_UPSTREAM_PROVIDER=compatible-endpoint \
    NEMOCLAW_INFERENCE_API=openai-completions
ARG NEMOCLAW_TOOL_DISCLOSURE=progressive
ENV NEMOCLAW_TOOL_DISCLOSURE=${NEMOCLAW_TOOL_DISCLOSURE}
RUN HOME=/sandbox node /opt/nemoclaw-hermes-config/generate-config.ts \
    && install -o root -g root -m 0444 /sandbox/.hermes/managed-policy.json /usr/local/share/nemoclaw/hermes-managed-policy.json \
    && rm /sandbox/.hermes/managed-policy.json \
    && chown sandbox:sandbox /sandbox/.hermes/config.yaml /sandbox/.hermes/.env \
    && chmod 0640 /sandbox/.hermes/config.yaml /sandbox/.hermes/.env
WORKDIR /sandbox
USER sandbox
