FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5 AS builder
ARG DEBIAN_FRONTEND=noninteractive
COPY third_party/apt-build.lock /tmp/apt-build.lock
RUN apt-get update && xargs -r apt-get install -y --no-install-recommends < /tmp/apt-build.lock \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
COPY src ./src
COPY third_party ./third_party
RUN g++-13 -std=c++20 -O3 -DNDEBUG -Wall -Wextra -Wpedantic -Wconversion -Wshadow \
    -Ithird_party -Isrc src/main.cpp src/sql.cpp src/storage.cpp -o /minivectordb \
    && dpkg-query -W > /build-packages.txt

FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5
WORKDIR /app
COPY --from=builder /minivectordb /app/minivectordb
COPY --from=builder /build-packages.txt /app/build-packages.txt
RUN mkdir /data
VOLUME ["/data"]
ENTRYPOINT ["/app/minivectordb"]
CMD ["--data-dir", "/data"]
