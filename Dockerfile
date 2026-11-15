# MiniVectorDB —— 赛题一提交镜像（仓库根 Dockerfile 为占位文件，这里给出实际构建）
# 构建：docker build -t minivectordb:latest .
FROM rust:1-slim-bookworm AS build
WORKDIR /src
COPY minivectordb/Cargo.toml ./
COPY minivectordb/src ./src
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/mvdb /usr/local/bin/mvdb
ENV MVDB_DATA=/data
VOLUME ["/data"]
ENTRYPOINT ["/usr/local/bin/mvdb"]
