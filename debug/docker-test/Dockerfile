FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update && apt-get install -y \
    python3 python3-venv python3-pip \
    nginx postgresql postgresql-client \
    systemctl sudo curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/log/nginx /var/log/postgresql && \
    echo '2026-04-01 10:00:00 Test log entry 1' > /var/log/nginx/access.log && \
    echo '2026-04-01 10:01:00 [error] test error' > /var/log/nginx/error.log && \
    echo '2026-04-01 10:00:00 UTC LOG: test postgres log' > /var/log/postgresql/postgresql-14-main.log && \
    echo 'Apr  1 10:00:00 test syslog message' > /var/log/syslog && \
    echo 'Apr  1 10:00:00 test auth message' > /var/log/auth.log

RUN mkdir -p /etc/postgresql/14/main && \
    echo "listen_addresses = 'localhost'" > /etc/postgresql/14/main/postgresql.conf && \
    echo "local all all peer" > /etc/postgresql/14/main/pg_hba.conf

COPY . /srv/serverlens
WORKDIR /srv/serverlens

COPY debug/docker-test/test-install.sh /test-install.sh
COPY debug/docker-test/test-migration.sh /test-migration.sh
RUN chmod +x /test-install.sh /test-migration.sh

CMD ["/test-install.sh"]
