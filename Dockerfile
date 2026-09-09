FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5

# Pin Helm to a specific version for reproducible builds (issue #762).
# The previous pattern fetched the latest release via the GitHub API at build
# time, meaning different builds could install different Helm versions.
# Update ARG HELM_VERSION when upgrading Helm deliberately.
ARG HELM_VERSION=4.2.3

# Pin kubectl to a specific version for reproducible builds.
# The previous pattern fetched the latest release via dl.k8s.io/release/stable.txt
# at build time, meaning different builds could install different kubectl versions
# and an attacker who can tamper with stable.txt could inject a malicious binary.
# Update ARG KUBECTL_VERSION when upgrading kubectl deliberately.
ARG KUBECTL_VERSION=1.36.3

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fLO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl" && \
    curl -fLO "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" && \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl kubectl.sha256 && \
    curl -fLO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" && \
    curl -fLO "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" && \
    sha256sum --check "helm-v${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" && \
    tar -xzf "helm-v${HELM_VERSION}-linux-amd64.tar.gz" linux-amd64/helm && \
    install -o root -g root -m 0755 linux-amd64/helm /usr/local/bin/helm && \
    rm -rf "helm-v${HELM_VERSION}-linux-amd64.tar.gz" "helm-v${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" linux-amd64 && \
    apt-get purge -y curl && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "PyYAML>=6.0,<7"

# Create a non-root user for the orchestrator process.
# Running as root is unnecessary and increases the blast radius if the
# process is compromised while it holds cluster credentials.
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup --no-create-home --shell /bin/false appuser

WORKDIR /app
COPY pipeline/ pipeline/

# Drop privileges: run as non-root appuser (UID 1001)
USER 1001

ENTRYPOINT ["python", "pipeline/deploy.py"]
