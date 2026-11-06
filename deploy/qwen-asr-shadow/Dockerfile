ARG PYTORCH_BASE=nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9
FROM ${PYTORCH_BASE}
ARG HOME_COMPUTE_REVISION
ARG ENGINE_SHA256=64a56a998c1d0e1a39eece7082b3531a02f565fd3f78bb495a7c820564af88d3
LABEL org.opencontainers.image.source="https://github.com/madslundt/HomeCompute" \
      org.opencontainers.image.revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.source-revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.qwen-asr.scope="isolated-shadow-only" \
      io.homecompute.qwen-asr.model-sha256="2db53c7d81bd9b8cbc6a074e89be2c968a0d373fb4ee68bb1b1e14f7042dfee1" \
      io.homecompute.qwen-asr.model-snapshot="bcd2b5b7f32b480ab5790554cfa8347f246a14f3" \
      io.homecompute.qwen-asr.runtime-commit="446a570e62b04a89ea001604ada7366baac2ea67" \
      io.homecompute.qwen-asr.engine-sha256="${ENGINE_SHA256}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    CUBLAS_WORKSPACE_CONFIG=:4096:8 NUMBA_CACHE_DIR=/tmp/numba \
    HOME=/tmp HF_HOME=/tmp/hf TORCH_HOME=/tmp/torch XDG_CACHE_HOME=/tmp/cache \
    HOME_COMPUTE_REVISION=${HOME_COMPUTE_REVISION}
WORKDIR /opt/homecompute
COPY requirements.lock /opt/homecompute/requirements.lock
# Only six exact public wheels are installed. No dependency resolver, source
# builds or isolated build environment can replace vendor Torch or CUDA.
RUN python3 -I -B -c 'import importlib.metadata as m,platform,sys; assert platform.machine()=="aarch64"; assert sys.version_info[:2]==(3,12); assert m.version("torch")=="2.11.0a0+eb65b36914.nv26.2"; assert m.version("torchvision")=="0.25.0a0+1e53952f.nv26.2.44259020"' \
 && python3 -m pip --isolated --disable-pip-version-check install --break-system-packages --no-index --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r requirements.lock
COPY model-pin.json model_files.py engine.py protocol.py adapter.py cpu_smoke.py /opt/homecompute/
# Check actual package/source identity with no CUDA initialization or model load.
RUN CUDA_VISIBLE_DEVICES="" python3 -I -B -c 'import hashlib,importlib.metadata as m,json,os,re; from pathlib import Path; import torch,transformers; from transformers import AutoProcessor,Qwen3ASRForConditionalGeneration,Qwen3ASRProcessor; p=json.loads(Path("model-pin.json").read_text()); assert all(m.version(k)==v for k,v in p["packages"].items()); assert Qwen3ASRForConditionalGeneration.__module__=="transformers.models.qwen3_asr.modeling_qwen3_asr"; assert Qwen3ASRProcessor.__module__=="transformers.models.qwen3_asr.processing_qwen3_asr"; root=Path(transformers.__file__).parent; assert all(hashlib.sha256((root/k).read_bytes()).hexdigest()==v for k,v in p["runtime_files"].items()); assert p["engine_sha256"]==hashlib.sha256(Path("engine.py").read_bytes()).hexdigest(); assert not torch.cuda.is_initialized(); assert re.fullmatch("[0-9a-f]{40}",os.environ["HOME_COMPUTE_REVISION"])' \
 && test "${ENGINE_SHA256}" = "$(sha256sum engine.py | cut -d ' ' -f 1)" \
 && groupadd --gid 10001 hc-qwen-asr \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin hc-qwen-asr \
 && mkdir -p /models \
 && chmod 0555 /opt/homecompute /models \
 && chmod 0444 /opt/homecompute/requirements.lock /opt/homecompute/model-pin.json \
 && chmod 0555 /opt/homecompute/*.py
USER 10001:10001
ENTRYPOINT ["python3", "-I", "-B", "/opt/homecompute/adapter.py"]
