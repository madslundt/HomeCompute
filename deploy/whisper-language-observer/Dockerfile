# Build only after explicit root review. The base must already exist locally.
ARG BASE_IMAGE=sha256:c0e6c0b168faa1be027564e9a004e842c342e22ea51903d5fc7b9008850398d0
FROM --platform=linux/arm64 ${BASE_IMAGE}
ARG HOME_COMPUTE_REVISION
ARG OBSERVER_BUILD_OWNER
LABEL io.homecompute.language-observer.build-owner="${OBSERVER_BUILD_OWNER}" \
      io.homecompute.source-revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.language-observer.scope="experimental-cpu-classification-only" \
      io.homecompute.language-observer.base="sha256:c0e6c0b168faa1be027564e9a004e842c342e22ea51903d5fc7b9008850398d0"
USER root
COPY wheels/ /opt/observer-wheels/
COPY common.py graph.py export.py classify.py installer_check.py model.json dependencies.json requirements.lock /opt/language-observer/
ENV CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=void
RUN python3 -I -B /opt/language-observer/installer_check.py snapshot /tmp/nvidia-packages.json \
 && python3 -m venv --without-pip /opt/observer-export \
 && python3 -m pip --python /opt/observer-export/bin/python install --no-index --no-deps --no-compile --require-hashes --find-links /opt/observer-wheels -r /opt/language-observer/requirements.lock \
 && env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME /opt/observer-export/bin/python -I -B /opt/language-observer/installer_check.py export \
 && python3 -I -B /opt/language-observer/installer_check.py unchanged /tmp/nvidia-packages.json \
 && rm -rf /opt/observer-wheels /tmp/nvidia-packages.json \
 && chmod -R a-w /opt/language-observer /opt/observer-export
ENV HOME_COMPUTE_REVISION=${HOME_COMPUTE_REVISION} \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=void \
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER 10001:10001
ENTRYPOINT ["python3", "-I", "-B", "/opt/language-observer/classify.py"]
