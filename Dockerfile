FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    KUBE_VERSION=$(curl -fsSL https://dl.k8s.io/release/stable.txt) && \
    curl -fLO "https://dl.k8s.io/release/${KUBE_VERSION}/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl && \
    apt-get purge -y curl && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir PyYAML

WORKDIR /app
COPY pipeline/ pipeline/

ENTRYPOINT ["python", "pipeline/deploy.py"]
