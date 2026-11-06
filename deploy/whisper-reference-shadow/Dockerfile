ARG PYTORCH_BASE=nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9
FROM ${PYTORCH_BASE}
ARG HOME_COMPUTE_REVISION
ARG LOADER_SHA256=1c9f6f75cc300e356b9b1c764c5836741aa7c6dfc47c5e38ee25eacfda34467c
LABEL org.opencontainers.image.source="https://github.com/madslundt/HomeCompute" \
      org.opencontainers.image.revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.source-revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.whisper-reference.scope="isolated-shadow-only" \
      io.homecompute.whisper-reference.model-sha256="64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2" \
      io.homecompute.whisper-reference.model-snapshot="c521a4b02f422512d734391fdf08bb08c0862f68" \
      io.homecompute.whisper-reference.runtime-commit="31243bad24cc746f07d4c8bfdd2d974872cb1803" \
      io.homecompute.whisper-reference.loader-sha256="${LOADER_SHA256}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    CUBLAS_WORKSPACE_CONFIG=:4096:8 NUMBA_CACHE_DIR=/tmp/numba \
    HOME_COMPUTE_REVISION=${HOME_COMPUTE_REVISION}
WORKDIR /opt/homecompute
COPY requirements.lock /opt/homecompute/requirements.lock
# No dependency resolver or isolated build environment may replace vendor Torch/CUDA.
# The only source distribution is the pinned pure-Python official Whisper package;
# its build uses the cached base's setuptools/wheel, with no build dependencies fetched.
# Explicit PEP668 consent applies only inside this image; --isolated ignores base pip environment settings.
RUN python3 -I -B -c 'import importlib.metadata as m,json,platform,sys; assert platform.machine()=="aarch64"; assert sys.version_info[:2]==(3,12); expected={"torch":"2.11.0a0+eb65b36914.nv26.2","torchvision":"0.25.0a0+1e53952f.nv26.2.44259020","numpy":"2.1.0","numba":"0.63.1","llvmlite":"0.46.0","regex":"2026.1.15","requests":"2.32.5","tqdm":"4.67.3","setuptools":"81.0.0","wheel":"0.42.0"}; assert all(m.version(k)==v for k,v in expected.items()); open("/tmp/base-versions.json","w").write(json.dumps(expected))' \
 && python3 -m pip --isolated --disable-pip-version-check install --break-system-packages --no-index --no-cache-dir --no-deps --no-build-isolation --require-hashes --only-binary=tiktoken,more-itertools -r requirements.lock \
 && python3 -I -B -c 'import importlib.metadata as m,json; expected=json.load(open("/tmp/base-versions.json")); assert all(m.version(k)==v for k,v in expected.items()); assert m.version("openai-whisper")=="20250625"; assert m.version("tiktoken")=="0.12.0"; assert m.version("more-itertools")=="10.8.0"' \
 && rm /tmp/base-versions.json
COPY model-pin.json ggml_loader.py engine.py protocol.py adapter.py /opt/homecompute/
# Import and verify package assets only. No model, CUDA context, audio or inference.
RUN CUDA_VISIBLE_DEVICES="" python3 -I -B -c 'import hashlib,json,os,re,sys; from pathlib import Path; sys.path.insert(0,"/opt/homecompute"); import torch,numpy,whisper,engine; from whisper.tokenizer import get_tokenizer,LANGUAGES; assert not torch.cuda.is_initialized(); engine.verify_reference_assets(Path(whisper.__file__).parent,numpy); resolved=engine.resolve_suppression_tokens(get_tokenizer(True,num_languages=100),LANGUAGES); assert resolved==list(range(50258,50365)); pin=json.loads(Path("model-pin.json").read_text()); assert pin["runtime_commit"]==engine.REFERENCE_COMMIT; assert pin["decoding_defaults"]==json.loads(json.dumps({**engine.DECODING_OPTIONS,"suppress_tokens":resolved,"language":None})); assert pin["loader_version"]==engine.VERSION; assert pin["loader_sha256"]==hashlib.sha256(Path("ggml_loader.py").read_bytes()).hexdigest(); assert re.fullmatch("[0-9a-f]{40}",os.environ["HOME_COMPUTE_REVISION"]); assert not torch.cuda.is_initialized()' \
 && test "${LOADER_SHA256}" = "$(sha256sum ggml_loader.py | cut -d ' ' -f 1)" \
 && groupadd --gid 10001 hc-whisper-ref \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin hc-whisper-ref \
 && mkdir -p /models \
 && chmod 0555 /opt/homecompute /models \
 && chmod 0444 /opt/homecompute/requirements.lock /opt/homecompute/model-pin.json \
 && chmod 0555 /opt/homecompute/ggml_loader.py /opt/homecompute/engine.py /opt/homecompute/protocol.py /opt/homecompute/adapter.py
USER 10001:10001
ENTRYPOINT ["python3", "-I", "-B", "/opt/homecompute/adapter.py"]
