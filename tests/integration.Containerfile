# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

FROM ubuntu@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=secret,id=host_ca,mode=0444 \
    sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get -o Acquire::ForceIPv4=true \
        -o Acquire::https::CaInfo=/run/secrets/host_ca update \
    && apt-get -o Acquire::ForceIPv4=true \
        -o Acquire::https::CaInfo=/run/secrets/host_ca \
        install --yes --no-install-recommends \
        apt-utils \
        ca-certificates \
        createrepo-c \
        dpkg-dev \
        file \
        gnupg \
        python3 \
        rpm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
ENTRYPOINT ["bash", "tests/integration.sh"]
