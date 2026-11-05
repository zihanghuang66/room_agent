#!/bin/bash
# Install development tools only; never install or replace the host NVIDIA driver.
set -euo pipefail
if [[ -x /usr/local/cuda-12.4/bin/nvcc ]] && \
   /usr/local/cuda-12.4/bin/nvcc --version | grep -q 'release 12.4,' && \
   [[ -f /usr/local/cuda-12.4/include/cuda_runtime.h && \
      -e /usr/local/cuda-12.4/lib64/stubs/libcuda.so && \
      -e /usr/local/cuda-12.4/lib64/libnvrtc.so ]]; then
    echo 'Existing CUDA 12.4 compiler, headers, driver stub and NVRTC development library found.'
else
    curl --fail --location --retry 5 --connect-timeout 30 --max-time 300 \
      -o /opt/room/build-info/cuda-keyring.deb \
      https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
    echo 'd93190d50b98ad4699ff40f4f7af50f16a76dac3bb8da1eaaf366d47898ff8df  /opt/room/build-info/cuda-keyring.deb' | sha256sum -c -
    dpkg --force-confdef --force-confold -i /opt/room/build-info/cuda-keyring.deb
    apt-get update
    apt-get install -y --no-install-recommends cuda-toolkit-12-4=12.4.1-1
    rm -f /opt/room/build-info/cuda-keyring.deb
    rm -rf /var/lib/apt/lists/*
fi
/usr/local/cuda-12.4/bin/nvcc --version | tee /opt/room/build-info/selected-nvcc.txt
grep -q 'release 12.4,' /opt/room/build-info/selected-nvcc.txt
test -f /usr/local/cuda-12.4/include/cuda_runtime.h
test -e /usr/local/cuda-12.4/lib64/stubs/libcuda.so
test -e /usr/local/cuda-12.4/lib64/libnvrtc.so
