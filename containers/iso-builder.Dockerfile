ARG DEBIAN_CONTAINER=debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
FROM ${DEBIAN_CONTAINER}
ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    apt-utils ca-certificates curl debian-keyring dpkg-dev gpg gpg-agent gpgv \
    build-essential cpio gzip python3 shellcheck syslinux-common tar xorriso xz-utils \
    && apt-get build-dep -y --no-install-recommends \
       -Ppkg.cdebconf.nogtk cdebconf=0.280 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
ENTRYPOINT ["/bin/bash", "/src/scripts/build-inside-container.sh"]
