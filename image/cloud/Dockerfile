# Parent is the already cached, previously tested Debian QEMU tool image.
# Build manifest records immutable parent and resulting image IDs.
ARG VM_TEST_IMAGE=rougarou-vm-test:trixie
FROM ${VM_TEST_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends gpgv openssh-client python3-yaml guestfish linux-image-amd64 zerofree \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["python3"]

ENV LIBGUESTFS_BACKEND=direct
