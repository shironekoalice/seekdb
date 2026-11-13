# Pinned dependencies

- nlohmann/json v3.11.3, single include `nlohmann/json.hpp`; MIT license alongside.
  SHA-256 `9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6`.
  Source: github.com/nlohmann/json, tag v3.11.3.
- Build/runtime base: ubuntu:24.04 digest
  `sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5`.
- `apt-build.lock` pins every changed/new package relative to that base,
  including GCC 13.3.0-6ubuntu2~24.04.1 and transitive compiler/libc headers.
  Build package inventory is retained in `/app/build-packages.txt` in the image.
- Docker build needs Ubuntu package mirrors; the database runtime is offline.
  If pinned packages disappear from those mirrors, building fails explicitly.
  No silent compiler upgrade or download-at-runtime fallback is used.
- Native testing uses the available WSL GCC13.3/CMake3.28/Python3; it is a
  development route, not a substitute for the source-built submission image.
