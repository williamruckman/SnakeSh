FROM ubuntu:22.04

ARG LINUXDEPLOY_URL="https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
ARG APPIMAGETOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV QT_QPA_PLATFORM=offscreen

RUN printf '%s\n' \
        'Acquire::Retries "5";' \
        'Acquire::http::Timeout "30";' \
        'Acquire::https::Timeout "30";' \
        > /etc/apt/apt.conf.d/80-snakesh-retries \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        build-essential \
        ca-certificates \
        curl \
        desktop-file-utils \
        file \
        gnupg \
        libegl1 \
        libfontconfig1 \
        libfuse2 \
        libgl1 \
        libopengl0 \
        libpulse0 \
        libwayland-client0 \
        libwayland-cursor0 \
        libwayland-egl1 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-util1 \
        libxrandr2 \
        libxkbcommon-x11-0 \
        patchelf \
        software-properties-common \
        squashfs-tools \
        xz-utils \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/snakesh/tools \
    && curl --fail --show-error --location --retry 5 --retry-connrefused --retry-max-time 180 --connect-timeout 15 --max-time 120 \
        "${LINUXDEPLOY_URL}" -o /opt/snakesh/tools/linuxdeploy-x86_64.AppImage \
    && curl --fail --show-error --location --retry 5 --retry-connrefused --retry-max-time 180 --connect-timeout 15 --max-time 120 \
        "${APPIMAGETOOL_URL}" -o /opt/snakesh/tools/appimagetool-x86_64.AppImage \
    && chmod +x /opt/snakesh/tools/linuxdeploy-x86_64.AppImage /opt/snakesh/tools/appimagetool-x86_64.AppImage

WORKDIR /workspace
