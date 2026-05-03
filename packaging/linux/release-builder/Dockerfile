FROM ubuntu:22.04

ARG LINUXDEPLOY_URL="https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
ARG APPIMAGETOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV QT_QPA_PLATFORM=offscreen

RUN apt-get update \
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
    && curl -L "${LINUXDEPLOY_URL}" -o /opt/snakesh/tools/linuxdeploy-x86_64.AppImage \
    && curl -L "${APPIMAGETOOL_URL}" -o /opt/snakesh/tools/appimagetool-x86_64.AppImage \
    && chmod +x /opt/snakesh/tools/linuxdeploy-x86_64.AppImage /opt/snakesh/tools/appimagetool-x86_64.AppImage

WORKDIR /workspace
