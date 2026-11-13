# B01 bounded optimization record

Status: completed. Started 2026-11-13 23:47:56 +08:00; maximum three
candidates / 45 minutes, with time reserved for cleanup and evidence.
The user explicitly resumed in the same conversation. Local optimization only;
no push, contest submission, model change or background optimizer.
Actual closure timestamp and elapsed audit time are recorded in `closure.json`.

## Preserved control

- Frozen tag `prep-baseline-20261113`, commit
  `86adad6257aa425c7f1985fa964457fe7390dbfe`.
- Source SHA256
  `4c7836b5081215d2ed849aa8a33e3d46cb3538a9b0e2cf4cfa91b7ea1d069e3d`;
  executable SHA256
  `3c022c9485466492d18216e8a44d7c7d16c1a35d2b8c68820b2a7225cc9d4c2c`.
- Baseline image ID
  `sha256:233191784d759ee3058d8d88f48ba2b77d0a123c62a0644a882bc30a39817ae2`.
- Candidate worktree `../../worktrees/minivectordb-b01`, initially based on
  `b221d26a6fcb61e8fbc7d60be88fe72c09c4d15b`. Each candidate starts from
  that frozen engine, without accumulating the previous unadopted changes.

## Candidate decisions

| Candidate | Hypothesis | Validation | Local E vs frozen / current | Decision |
| --- | --- | --- | --- | --- |
| P01 | Bind SELECT predicates once | Native 20 public + 11 groups; Docker 20 + 5; targeted eager-error/operator probe | 0.491478 / 0.503032 | Inconclusive, not adopted |
| P03 | Use indexed vectors before loading full TopK payload | Native/Docker/sanitizer public + extended; 119-query oracle on all three; 128 MiB / 256 MiB live data | 0.487888 / 0.499438 | Rejected |
| P04 | Cache the fixed disk-map bucket directory | Native/Docker/sanitizer public + extended; query/collision oracles; small/full live data over memory; two seeds | 0.503876 / 0.515368; second seed 0.499212 | Inconclusive, not adopted |

The three full-load reports use 10000 rows, 128 dimensions, 1000 exact
queries, half with equality filters, K10, 100-row committed batches and seed
20261113. All rounds verify persisted row contents and both recall groups are
1.0. Every report is kept, including negative results and individual rounds.

| Full workload | Write median seconds | Query median seconds |
| --- | ---: | ---: |
| Frozen baseline | 2.447856787 | 9.201286106 |
| Contemporary baseline | 2.625944038 | 9.949441645 |
| P01 | 2.765159925 | 9.675605827 |
| P03 | 2.761876046 | 9.698443071 |
| P04, first seed | 2.897391242 | 8.150893272 |
| Baseline, second seed | 2.689260663 | 9.985278633 |
| P04, second seed | 3.110247668 | 9.385507248 |

P01's short rehearsal gains did not establish a full-workload benefit. The
contemporary baseline itself varied, so a small favorable E against it is not
enough to adopt the change. P03 preserved correctness but did not improve
aggregate efficiency. Full-row deferral still performs a disk-map lookup per
index entry, so the hypothesis did not remove that repeated work.
P04 improved the first query sequence, but the second seed's three candidate
query times were 10.524761, 9.385507 and 7.284585 seconds, against the second
baseline's 9.995557, 9.985279 and 9.920191 seconds. All rounds count; the
second-seed aggregate E was 0.499212. This does not establish repeatable
aggregate benefit, so the frozen engine remains best.

## P04 mechanism and limits

The earlier baseline profile recorded 51006 DiskMap::find calls and 108720
File::read calls on the rehearsal workload. Code inspection found an
unconditional eight-byte bucket-head read on each lookup/set. This establishes
repeated work; the 0.01-second profile does not establish reliable time shares.
Repeating that profile workload with P04 counted the same 51006 lookups and
56205 File::read calls. Its zero sampled CPU seconds likewise do not establish
time shares; the counts confirm fewer reads, not repeatable aggregate speedup.

P04 keeps the 8192 bucket heads in a zero-initialized 64 KiB array per live
DiskMap, updates the cached head after the disk header is written, and retains
disk-resident chain nodes. Derived maps still truncate and rebuild from the
authoritative log at startup. WAL, commit acknowledgement and file format are
unchanged. Memory depends on live map count: this is not a globally capped
64 KiB cache.

Independent input generation creates 24 keys in one bucket, while expected
SQL state uses a Python dictionary/full sort. The checks cover duplicate keys,
long chains, pending changes, delete/reinsert, failed batch atomicity,
rollback, committed tombstones, process kill and repeated recovery. The
control, candidate and sanitizer passed. The separate query oracle covers
119 comparisons across no-index/index/stale/transaction/rollback/commit/restart,
including non-leading and signed 64-bit PKs, Unicode, filter operators,
different distance expressions, mixed orders, DESC, payloads and LIMIT 0.
P04's 2 GiB / 2304 MiB live-data check passed with 36864 rows, no OOM or host
swap, 33.019272-second recovery and 244.157184-second total runtime. This is a
correctness/resource-cap observation, not a recovery-speed improvement over
the separately timed frozen baseline.

## Measurement boundaries

Docker source builds use the unchanged pinned Dockerfile and compiler.
Performance runs are sequential, without concurrent builds or this task's
large-data checks. Other user services remain running. No global cache reset,
swap disabling, CPU affinity change or resource expansion.

Local cgroup B includes physical reads+writes and M includes page cache;
startup, loading, index preparation, warmup, shutdown and sync are included.
T is client wall time. WSL cannot enforce the requested swap limit
(`memory.swap.max=max`); measured host swap activity and OOM counters must be
reported. No official reference, hidden 29-case result, rank or Agent score is
inferred from these measurements.

## Tool observations

The first Windows-to-WSL command lost its shell output variable during
quoting. P01 logs were generated under `/p01-*`. After both processes completed,
the exact files/directories were moved to this evidence root; their contents
and embedded original paths were retained. Later commands use explicit paths.
The Windows-created worktree has a Windows `.git` pointer, so git operations
use Windows git; Linux build and validation do not need to rewrite that pointer.

Student evidence remains incomplete. The resume instruction is a real student
organizational decision. Hypotheses, implementation, tests and result
interpretation here are Agent/tool work, not proof of student understanding.
