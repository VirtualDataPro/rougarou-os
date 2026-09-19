ARG DEBIAN_CONTAINER=debian:trixie-slim
FROM ${DEBIAN_CONTAINER}
RUN apt-get update && apt-get install -y --no-install-recommends \
    qemu-system-x86 qemu-utils ovmf seabios python3 python3-pexpect \
    xorriso cpio gzip openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["python3", "/src/tests/vm/validate.py"]
