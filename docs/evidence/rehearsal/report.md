# Independent MiniVectorDB handoff rehearsal

Decision: **inconclusive; candidate not adopted**. One candidate was implemented
only in this directory. The two tiny screening pairs favor it, but do not
establish full-workload benefit. Stop here for the user's same-conversation
model switch; no background optimizer, push or official submission was started.

Started: 2026-11-13 23:28:04 +08:00. Budget: one candidate / eight minutes.
Performance execution and identity capture completed before 23:33:48.
The final packaging timestamp and elapsed duration are in `audit.json`.

## Hypothesis and identity

P01: bind SELECT predicate column indexes and literal values once before scanning.
The only engine edit is `source/src/storage.cpp`, captured in `candidate.patch`.
Validation still runs on empty tables and LIMIT 0; predicates retain their order.
UPDATE/DELETE, row visibility, WAL, index traversal and distance computation were
not modified. Bound memory depends on the SQL predicates, not table row count.

- Baseline commit/tag: `86adad6257aa425c7f1985fa964457fe7390dbfe` /
  `prep-baseline-20261113`.
- Baseline code SHA256: `4c7836b5081215d2ed849aa8a33e3d46cb3538a9b0e2cf4cfa91b7ea1d069e3d`.
- Candidate code SHA256: `7a6d285d6fcb5251b6d1fa0f12716383e02d5f2e80cdc1a91ac9f61d2632932c`.
- Baseline image: `sha256:233191784d759ee3058d8d88f48ba2b77d0a123c62a0644a882bc30a39817ae2`.
- Candidate image: `minivectordb:rehearsal-p01-20261113-2331`,
  `sha256:81f4a154a4ed24293dce6df97952a596451c84e83910006900f78630c58dfa83`.
- Binary hashes: `binary-identities.sha256`; isolated builds:
  `/opt/mvdb-rehearsal-build/profile` and `/opt/mvdb-rehearsal-build/candidate`.

The profile build used unmodified baseline source and `-pg`. A 500-row,
16-dimensional, 100-query workload produced 50,021 `matches`, 26,595 `literal`,
and 60,591 `Schema::field` calls. This confirms repetition. The 0.01-second
sample is insufficient for reliable attribution of execution time.

## Validation and screening

Candidate probe passed six error cases on empty tables (including LIMIT 0),
all five comparison operators, AND, and long VARCHAR comparison literals.
Independent `types`, `ordering`, `random_model`, and `transaction` checks passed.
The randomized check includes 240 operations, 48 state comparisons and 48
independent filtered TopK comparisons. Ordering includes ties, stale/deleted
index entries and restart; transaction includes read-your-writes and crashes.

Micro workload: 1000 rows x 16 dimensions, 100 exact queries, K10, half filtered,
seed 20261113, 100-row write batches. Each run uses a fresh container/data volume,
2 CPU and 2 GiB. Runs were sequential, baseline/candidate/candidate/baseline.
All four runs passed the independent C++ full-sort oracle and distance checks;
both recall groups were 1.0, and each write run verified 1000 rows after restart.

| Run | Write T (s) | Query T (s) | Write B (bytes) | Query B (bytes) | Write M (bytes) | Query M (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline 1 | 0.146623 | 0.137418 | 1847296 | 1941504 | 6291456 | 6537216 |
| Candidate 1 | 0.145515 | 0.126361 | 1847296 | 1941504 | 6103040 | 6582272 |
| Candidate 2 | 0.133143 | 0.118567 | 1847296 | 1941504 | 6778880 | 6574080 |
| Baseline 2 | 0.107193 | 0.127514 | 1847296 | 1941504 | 6303744 | 6361088 |

M is cgroup peak including page cache, not RSS. B/M include load, index,
warmup, shutdown and sync. WSL swap limiting is unsupported (`memory.swap.max=max`);
all recorded swap-in/out deltas and OOM counts were zero. Host caches were
not reset. Sub-second samples and visible write-time variation limit inference.
No E score or three-round full-workload comparison was computed.

## Actual command sequence

Commands ran through `wsl -d Ubuntu-24.04 -- bash -lc ...`, in this directory.
`TMPDIR` was set to this directory's `tmp`; `PYTHONDONTWRITEBYTECODE=1`.
Only `src`, `third_party`, `tools`, `tests`, `protocol_check`, `mysqltest`,
`CMakeLists.txt`, and `Dockerfile` were copied into `source`, excluding `.git`
and the original `artifacts`.

```sh
python3 source/tools/fingerprint.py
cmake -S source -B /opt/mvdb-rehearsal-build/profile -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS=-pg -DCMAKE_EXE_LINKER_FLAGS=-pg
cmake --build /opt/mvdb-rehearsal-build/profile --target minivectordb -j2
python3 probe.py --binary /opt/mvdb-rehearsal-build/profile/minivectordb --output profile-probe
gprof /opt/mvdb-rehearsal-build/profile/minivectordb gmon.out
# Apply the single candidate.patch, then:
cmake -S source -B /opt/mvdb-rehearsal-build/candidate -DCMAKE_BUILD_TYPE=Release
cmake --build /opt/mvdb-rehearsal-build/candidate --target minivectordb -j2
python3 probe.py --binary /opt/mvdb-rehearsal-build/candidate/minivectordb --output candidate-probe
python3 source/tests/contract.py --binary /opt/mvdb-rehearsal-build/candidate/minivectordb --check types --check ordering --check random_model --check transaction --output candidate-checks
cp /opt/mvdb-rehearsal-build/candidate/minivectordb screen-image/minivectordb
docker build -f Dockerfile.screen -t minivectordb:rehearsal-p01-20261113-2331 screen-image
```

For each of the four runs, the actual benchmark command was:

```sh
python3 source/tools/benchmark.py --image IMAGE --oracle /opt/minivectordb-build/mvdb_oracle --rows 1000 --dimensions 16 --queries 100 --rounds 1 --output OUTPUT
```

The IMAGE/OUTPUT pairs in order were `minivectordb:baseline`/`screen-baseline`,
candidate/`screen-candidate`, candidate/`screen-candidate-2`, and
`minivectordb:baseline`/`screen-baseline-2`. Their stdout/stderr logs use the
corresponding `.log` names. Configure/build logs and profile output are retained.
The experimental image copies the native Release binary over baseline; it is
not a source-only contest packaging validation.

## File inventory

- `record.json`: hypothesis, identities, checks, decision and stopping state.
- `candidate.patch`, `source/`: minimal diff and isolated source/tool copy.
- `inputs/`: handoff, state, queue, validation/baseline docs, installed skills,
  identity record, and untouched original learning-material copy.
- `probe.py`, `profile-probe/`, `candidate-probe/`, `candidate-checks/`:
  targeted counterexamples and independent semantic reports/logs.
- `profile-gprof.txt`, `gmon.out`, profile/build logs: raw call-count evidence.
- `screen-*/`: four raw reports, generated workload and independent oracle.
- `Dockerfile.screen`, `screen-image/`: experimental image construction inputs.
- `*-fingerprint.json`, `binary-identities.sha256`, `image-ids.txt`,
  `formal-status-after.txt`: identity and preservation evidence.
- `learning/conversation.json`, `learning-validation.json`: actual Agent/tool
  material and its structural validation; no new student contributions claimed.
- `audit.json`, `manifest.json`: final preservation checks and file hashes.

## Handoff/tool observations and unperformed work

1. Routing and the installed skills were sufficient to reach a working isolated
   experiment. Current one-candidate/eight-minute scope overrides the generic
   three-candidate/45-minute workflow and formal state-update instruction.
   Experiment state was therefore written only to this directory.
2. Native protocol helpers create temporary directories under the default temp
   root. An explicit `TMPDIR` is necessary for this task's write allowlist.
3. `git diff --no-index` correctly emitted the patch and returned 1 for a
   difference. The compound archival command propagated a nonzero exit before
   its final `cat`; reports, hashes and the patch were subsequently read directly.
   This was an archival-shell status issue, not a benchmark/test failure.
4. The profiling sample demonstrates calls, not time dominance. Existing
   benchmark output includes cgroup data and the WSL swap caveat as needed.
5. Full 20-case/public rerun, six-point crash matrix, sanitizer, out-of-core data,
   and three full local performance rounds were not run. This query-local
   rehearsal does not establish those properties for the candidate.
6. The original worktree already contained a state change and an untracked
   skill-installation report; they were not reverted. Formal code, installed
   skills, baseline image/tag and formal learning material were preserved.
7. Agent/tool work is recorded as such. No student speech, understanding or
   official learning score was invented.

The next model may consider P01 for a separately authorized full comparison.
This rehearsal is finished and does not authorize further execution.
