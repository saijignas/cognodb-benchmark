# CognoDB Graph Database Benchmark

## 1. Executive Summary

This repository benchmarks **CognoDB Cloud** against four other graph databases â€”
**Neo4j**, **Memgraph**, **ArangoDB**, and **SurrealDB** â€” on identical data,
identical workloads, and (with one disclosed exception) identical resource
ceilings, using a fully automated Python harness.

Headline findings: **Memgraph** was the fastest platform tested for both
ingest and the majority of latency workloads, with the tightest tail-latency
behavior. **ArangoDB** and **SurrealDB** each shipped with real, reproducible
correctness bugs in their `three_hop` traversal that only manifested at real
dataset scale (both are documented and fixed below). **Neo4j** could not
start at all under a 256 MiB memory ceiling and required explicit JVM tuning.
**CognoDB**'s latency for simple lookups is dominated by network round-trip
time to the remote managed instance rather than server-side execution cost.
Every one of these findings is backed by raw measurement data in this
repository, not assumption.

This README is written to be understandable without having watched the
development process â€” every non-obvious decision is explained with the
evidence behind it.

## 2. Databases Tested

| Platform | Role | Query language | Deployment |
|---|---|---|---|
| CognoDB Cloud | Primary subject | Cypher over Bolt | Managed cloud instance |
| Neo4j | Comparison | Cypher over Bolt | Local Docker container |
| Memgraph | Comparison | Cypher over Bolt | Local Docker container |
| ArangoDB | Comparison | AQL | Local Docker container |
| SurrealDB | Comparison | SurrealQL | Local Docker container |

CognoDB, Neo4j, and Memgraph all speak Bolt + Cypher and share a single
adapter implementation (`benchmark/adapters/bolt_adapter.py`) â€” confirmed via
CognoDB's own setup documentation that it is Bolt/Cypher-compatible.
ArangoDB and SurrealDB each have their own adapter, since AQL and SurrealQL
are structurally different query languages.

## 3. Dataset

**Source:** [MovieLens 100K](https://grouplens.org/datasets/movielens/100k/)
(GroupLens Research, University of Minnesota), downloaded automatically by
`scripts/preprocess_movielens.py` from the canonical GroupLens distribution.

**Exact counts** (independently verified against GroupLens's own `u.info`
file, and re-verified by `scripts/validate_dataset.py` on every run):

| | Count |
|---|---|
| Users | 943 |
| Movies | 1,682 |
| Genres | 19 (including "unknown", used by exactly 2 movies) |
| Ratings (`RATED` edges) | 100,000 (zero duplicate user/movie pairs) |
| Genre edges (`HAS_GENRE`) | 2,893 |
| **Total nodes** | **2,644** |
| **Total relationships** | **102,893** |

**Schema:** `User(id) -[:RATED {rating, timestamp}]-> Movie(id, title)`,
`Movie -[:HAS_GENRE]-> Genre(name)`.

The same dataset (byte-identical JSON, regenerated deterministically rather
than committed â€” see Reproducibility) was loaded into every platform.

## 4. Resource Configuration

**CPU / RAM / swap â€” identical and hard-enforced for all five platforms:**

- 0.5 vCPU
- 512 MiB RAM
- 512 MiB swap

For the four local platforms this is enforced via Docker cgroups
(`--cpus=0.5 --memory=512m --memory-swap=512m`) and independently confirmed
via `docker stats` and `OOMKilled` state during testing. For CognoDB, these
are the platform's own documented limits for the benchmark instance (Free
plan), read directly from the CognoDB Cloud dashboard for the actual
instance used (`db-d86bc947`):

- **vCPU:** burst to 0.5 vCPU
- **Memory:** 512 MB
- **Storage:** 1 GiB
- **Storage in use during testing:** 167 MB / 1 GiB

**Why 512 MiB, not the originally planned lower ceiling:** Neo4j 5 Community
Edition could not start at all under a 256 MiB cgroup limit â€” confirmed
empirically (`OOMKilled=true`, `ExitCode=137`) even with its heap and page
cache manually capped well below 256 MiB, because its JVM has fixed overhead
(metaspace, thread stacks, native buffers) independent of the configured
heap size. Since CognoDB's own dashboard documents 512 MB for the actual
benchmark instance, 512 MiB was adopted as the uniform ceiling for all five
platforms â€” this is not a concession to Neo4j, it is what the real CognoDB
instance already uses.

Neo4j additionally required explicit heap/page-cache tuning
(`heap_initial=128m`, `heap_max=192m`, `pagecache=64m`) because its
auto-sizing logic reads host-level RAM rather than the cgroup limit and
always overshoots regardless of the configured cap on this host.

**Storage â€” see Section 10 for the full, honest disclosure.** In short: a
hard 1 GiB storage quota could not be reliably enforced for the four local
platforms due to infrastructure limits outside this benchmark's control.

## 5. Deployment Architecture

- Local platforms run as disposable Docker containers, started fresh
  (`docker run`), benchmarked, and torn down (`docker rm -f`) one at a time
  â€” never concurrently with each other.
- CognoDB is accessed as a remote managed instance; credentials are supplied
  exclusively via environment variables (GitHub Codespaces repository
  secrets in this environment), never committed or logged.
- The benchmark runner (`benchmark/core/runner.py`) is entirely
  platform-agnostic â€” it drives every platform through the same
  `GraphDBAdapter` interface and contains no platform-specific logic.

## 6. Workload Definitions

All workload *semantics* are specified once in `benchmark/adapters/base.py`
and implemented per platform using each platform's native query language.

| Workload | Definition |
|---|---|
| Ingest | Bulk-load the full dataset; report wall-clock time, nodes/sec, relationships/sec |
| `point_lookup` | Fetch a single user by id |
| `indexed_lookup` | Fetch movies by an indexed, non-key property (title) |
| `one_hop` | Movies rated by the start user |
| `two_hop` | Other users (excluding the start user) who rated at least one movie the start user also rated |
| `three_hop` | Movies rated by any user in `two_hop`'s result set |
| `aggregation` | Count `HAS_GENRE` edges grouped by genre, ordered by count descending |
| `mixed_write` | Insert one new rating edge (used only by the concurrent workload) |
| Concurrent | 80% `one_hop` reads / 20% `mixed_write` writes, at a stated concurrency, for a stated duration |

**`two_hop`/`three_hop` required a genuine cross-platform correctness fix.**
Cypher's relationship-uniqueness rule (no single edge reused twice within one
`MATCH` pattern) silently produces different result *sets* than AQL's or
SurrealQL's plain traversal semantics for the same query shape. Rather than
accept this as an unavoidable "language difference" caveat, one common
logical workload was defined explicitly (co-raters excluding the start user,
then movies rated by that set) and implemented as an explicit staged
computation in every adapter â€” Cypher via two `MATCH` clauses joined by
`WITH`, AQL via an explicit `FILTER`, SurrealDB via `LET`/`array::distinct`
staging in one round trip. This was verified to produce **byte-identical
result sets**, not just superficially similar ones, across all three query
languages on a hand-verified synthetic dataset before ever touching real
data.

## 7. Warm-up and Iteration Methodology

For every latency workload:

1. **20 warm-up iterations** are run and discarded (not measured, not
   persisted) to let connection pools, query caches, and JIT/interpreter
   warm-up settle.
2. **100 measured iterations** follow, each timed individually with
   `time.perf_counter()`.
3. Query arguments (which user id, which movie title) are **sampled
   randomly** from the real dataset on every iteration (seeded for
   reproducibility), rather than repeatedly querying one fixed id â€” a fixed
   id risks measuring one platform's caching behavior for that specific
   value rather than representative cost.
4. p50/p95/mean/min/max/stddev are computed via linear-interpolation
   percentiles (`benchmark/core/timing.py`) over the 100 measured samples.
5. A query that throws an exception during measurement is recorded as an
   error (not silently dropped, not retried) and excluded from the latency
   statistics; error counts are reported alongside every latency result.

## 8. Concurrency Methodology

**Stated concurrency: 10 clients**, sustained for **30 seconds**, mixing 80%
`one_hop` reads and 20% `mixed_write` writes.

Each of the 10 workers runs as a **separate OS process**, not a thread â€”
this was not a stylistic choice: the SurrealDB Python client's asyncio event
loop is a process-wide singleton, and a second *thread* attempting to
connect while another thread's loop is running raises `RuntimeError: This
event loop is already running` (confirmed empirically). Using separate
processes for every platform's concurrency workers sidesteps thread-safety
assumptions about all four other client libraries uniformly, rather than
special-casing one platform.

All 10 workers synchronize via a shared `multiprocessing.Barrier` before the
timed window begins. Without this, process-spawn-plus-import-plus-connect
overhead for 10 processes can exceed a short measurement window entirely â€”
confirmed empirically: an earlier, unsynchronized 3-second window produced
**zero** completed operations because every worker was still connecting
when its deadline (computed before any process was even spawned) had
already passed. The reported `duration_seconds` is the actual synchronized
measurement window every connected worker ran for, not raw wall-clock time
including setup.

Each worker's initial connection retries up to 4 times with exponential
backoff: launching 10 processes that each open a fresh connection at nearly
the same instant can transiently exceed what a 0.5-CPU-capped server
accepts in that instant (confirmed empirically against SurrealDB at 4
concurrent connections). This is realistic client behavior (any real
concurrent client retries transient connection failures), not a way of
hiding a problem â€” exhausted retries are recorded as `connection_errors`,
never silently dropped.

**Correction applied before final submission:** the benchmark was initially
run and fully validated at 8 workers. The assignment requires a stated
concurrency in the **10â€“40** range. The concurrency-only workload (not the
full benchmark) was re-run at exactly **10 workers**, using the identical
runner code, barrier synchronization, read/write mix, and resource limits â€”
only the worker count changed. These 10-client results are the ones
reported as final in Section 9; the original 8-client measurements remain
in `results/raw/*_concurrent.jsonl` as untouched historical/engineering
evidence (see `results/concurrency-10client/` for the corrected data).

## 9. Full Results Matrix

*512 MiB RAM / 0.5 vCPU / 512 MiB swap for every platform. 100 measured
iterations per latency workload after 20 discarded warm-up iterations.
All raw data: `results/raw/*.jsonl` and `results/concurrency-10client/raw/*.jsonl`.*

### Ingestion

| Platform | Wall-clock | Nodes/sec | Relationships/sec |
|---|---|---|---|
| Memgraph | **2.75 s** | 962.6 | **37,458.8** |
| ArangoDB | 6.27 s | **422.0** | 16,422.7 |
| SurrealDB | 23.10 s | 114.5 | 4,455.2 |
| CognoDB | 24.73 s | 106.9 | 4,161.1 |
| Neo4j | 44.69 s | 59.2 | 2,302.4 |

### Traversals (ms)

| Platform | 1-hop p50 | 1-hop p95 | 2-hop p50 | 2-hop p95 | 3-hop p50 | 3-hop p95 |
|---|---|---|---|---|---|---|
| Memgraph | 2.340 | 6.254 | **26.778** | **52.529** | **76.384** | **99.797** |
| SurrealDB | **1.267** | **2.027** | 50.248 | 184.318 | 625.050 | 736.532 |
| Neo4j | 9.620 | 80.332 | 54.302 | 195.091 | 147.289 | 416.614 |
| ArangoDB | 2.529 | 6.579 | 500.807 | 1708.541 | 2398.576 | 3854.002 |
| CognoDB | 199.471 | 204.660 | 321.152 | 604.720 | 1016.364 | 1473.754 |

### Lookups (ms)

| Platform | Point lookup p50 | Point lookup p95 | Indexed lookup p50 | Indexed lookup p95 |
|---|---|---|---|---|
| Memgraph | **0.722** | **0.862** | **0.721** | 1.006 |
| SurrealDB | 0.844 | 1.153 | 1.328 | 1.906 |
| ArangoDB | 1.119 | 1.623 | 1.399 | **1.884** |
| Neo4j | 4.070 | 77.395 | 3.359 | 74.037 |
| CognoDB | 197.741 | 198.858 | 197.795 | 199.095 |

### Aggregation (ms)

| Platform | p50 | p95 |
|---|---|---|
| Memgraph | **2.975** | **21.079** |
| Neo4j | 4.876 | 76.252 |
| SurrealDB | 8.804 | 63.525 |
| ArangoDB | 72.512 | 80.312 |
| CognoDB | 204.152 | 207.031 |

### Concurrency â€” 10 clients, 30 seconds, 80% read / 20% write (final, required)

| Platform | Reads | Reads/s | Writes | Writes/s | Total ops/s | Errors |
|---|---|---|---|---|---|---|
| Memgraph | 10,710 | **357.0** | 2,730 | **91.0** | **448.0** | 0 |
| SurrealDB | 6,535 | 217.8 | 1,697 | 56.6 | 274.4 | 0 |
| ArangoDB | 6,172 | 205.7 | 1,606 | 53.5 | 259.3 | 0 |
| Neo4j | 1,997 | 66.6 | 503 | 16.8 | 83.3 | 0 |
| CognoDB | 1,122 | 37.4 | 269 | 9.0 | 46.4 | 0 |

*(Original 8-worker measurements, preserved as engineering evidence, not
the final reported metric: `results/raw/*_concurrent.jsonl`.)*

### Resource Footprint

| Platform | Footprint |
|---|---|
| ArangoDB | **Observable** â€” index size 10,290,464 bytes (~9.8 MB), document size 11,725,707 bytes (~11.2 MB), cache not in use |
| CognoDB | Not observable (managed instance, no introspection API used) |
| Neo4j | Not observable (no lightweight in-process API used) |
| Memgraph | Not observable (no lightweight in-process API used) |
| SurrealDB | Not observable (no lightweight in-process API used) |

## 10. Storage-Parity Limitation (read this before drawing conclusions)

**CognoDB's storage allocation is documented, not estimated:** 1 GiB, per the
CognoDB Cloud dashboard for the actual benchmark instance (`db-d86bc947`).
This number is not invented or extrapolated â€” it is read directly from the
provider's own management console.

**The four local platforms do NOT have a hard-enforced 1 GiB storage quota.**
A serious, multi-stage attempt was made to give them one, and it is
documented in full in `docs/known-issues.md`:

1. A 1 GiB loopback-backed ext4 filesystem mechanism (`fallocate` â†’
   `mkfs.ext4` â†’ `losetup` â†’ `mount`) was designed and **successfully
   proven end-to-end** in isolation â€” create, format, attach, mount, verify
   filesystem type and capacity, write/read a test file, unmount, detach,
   cleanup all worked correctly.
2. Sustaining that mechanism for the duration of a real benchmark run could
   not be achieved, for two independent, empirically confirmed reasons
   outside this benchmark's control:
   - GitHub Codespaces' **host-level `snapd`** process (running entirely
     outside the container's own namespace â€” no `snap` binary, no
     accessible `systemd`, confirmed via direct inspection) asynchronously
     and unpredictably consumes the small, fixed pool of loop devices
     (8 total) available inside the container.
   - The Codespace container itself is **not guaranteed to stay running
     continuously** even for a ~10-minute unattended window â€” an attempted
     stability test was interrupted by a full, unplanned container restart
     partway through (confirmed via `uptime` showing a fresh 7-minute boot
     against an expected ~75-minute-old process).
3. This was tested across **three separate fresh Codespace instances** to
   rule out an isolated fluke; the same class of limitation recurred.

**Given this, the four local platforms ran with unbounded access to the
shared Codespace disk (32 GB).** This is a genuine, disclosed gap against
the assignment's storage-parity requirement, not a hidden one. Two facts
bound its likely practical impact: the actual measured on-disk footprint of
this dataset is tiny (ArangoDB's own measured footprint: ~21 MB total for
data + indexes) against the 32 GB available, and CPU/RAM â€” the constraints
that *were* hard-enforced â€” were the ones that actually proved binding
during this benchmark (Neo4j's 256 MiB OOM, SurrealDB's three_hop OOM,
ArangoDB's CPU-bound three_hop hang). Storage capacity was never observed
to be a limiting factor for any platform at this dataset scale.

**Do not read the local platforms as having a 1 GiB storage quota â€” they do
not.** Any storage-fairness claim in this report is limited to: CPU and RAM
are genuinely equivalent across all five platforms; storage is not, and
this is disclosed rather than concealed.

## 11. CognoDB Network-Latency Caveat

CognoDB's latency for **every simple lookup workload** (`point_lookup`,
`indexed_lookup`, `one_hop`, `aggregation`) clusters tightly in a narrow
**197â€“207 ms** band, regardless of query complexity â€” compare this to local
platforms completing the same queries in low single-digit milliseconds or
less. This uniformity, essentially independent of what the query actually
does, is strong evidence that a roughly-constant network round-trip-time
floor to the remote managed instance dominates these measurements, not
server-side execution cost.

For multi-hop traversals, CognoDB's *additional* latency above its own
baseline (two_hop: ~124 ms above baseline; three_hop: ~819 ms above
baseline) plausibly reflects real additional server-side query cost layered
on top of the fixed network floor â€” but this cannot be cleanly decomposed
without server-side timing instrumentation, which is not available for a
managed remote instance. **CognoDB's absolute latency numbers should not be
read as a direct measure of its query engine's raw speed** â€” they conflate
engine performance with unavoidable network physics that the four local,
same-host platforms do not experience.

## 12. Neo4j Memory Limitation and Tuning

Neo4j 5 Community Edition could not start at all under a 256 MiB memory
cgroup â€” confirmed via `docker inspect`: `OOMKilled=true`, `ExitCode=137`,
reproduced even with heap explicitly capped at 96 MB and page cache at
32 MB (well below the 256 MiB ceiling itself). Neo4j's own memory
auto-sizing logic reads *host* RAM rather than the cgroup limit, so it
always computes a heap/page-cache recommendation that overshoots any small
container cap, regardless of what that cap actually is. The working fix:
explicit `NEO4J_server_memory_heap_initial__size=128m`,
`NEO4J_server_memory_heap_max__size=192m`,
`NEO4J_server_memory_pagecache_size=64m`, combined with raising the shared
ceiling to 512 MiB for all five platforms (matching what CognoDB's own
dashboard documents for the actual benchmark instance â€” see Section 4).

Even after this fix, Neo4j shows a markedly wider p50-to-p95 spread than
Memgraph on equivalent queries (e.g. `point_lookup`: p50 = 4.07 ms vs.
p95 = 77.4 ms, roughly a 19Ã— gap). This pattern â€” a fast typical case with
occasional much slower outliers â€” is consistent with periodic JVM
garbage-collection pauses, a well-documented general characteristic of
JVM-based systems. This is offered as a plausible, evidence-consistent
explanation, not a claim verified via internal profiling.

## 13. ArangoDB and SurrealDB: Implementation Issues Found and Corrected

Both issues were found only once the *real* MovieLens dataset replaced the
tiny synthetic dataset used for initial correctness testing â€” neither was
visible on synthetic data, because both depend on the dense, "small-world"
connectivity of real collaborative-filtering data.

### ArangoDB: `three_hop` combinatorial blowup

**Symptom:** hung for 90+ minutes on the real dataset before being manually
terminated. **Root cause:** the AQL query filtered out the start user but
never deduplicated the co-rater set before the final expansion stage â€” a
co-rater sharing *K* movies with the start user had their entire rated-movie
list re-traversed *K* times instead of once. **Fix:** staged the query with
an explicit `LET co_raters = (... RETURN DISTINCT u2 ...)` dedup step before
expansion, mirroring what the Cypher adapter already did via `WITH DISTINCT`.
**Verified via:** contract tests, a live correctness smoke test with exact
hand-derived expected values, and a full clean benchmark re-run completing
in ~7 minutes with zero errors. Full incident writeup: `docs/known-issues.md`.

### SurrealDB: OOM during `three_hop`, on two independent storage backends

**Symptom:** the server process itself was OOM-killed (`OOMKilled=true`)
partway through the benchmark, on **both** the default in-memory backend
and a RocksDB-backed configuration tried as a fix.

**Two independent bugs, both in `three_hop`:**
1. The same defect class as ArangoDB's â€” the co-rater set was never
   deduplicated before the final expansion. Fixed with `array::distinct()`
   wrapped around the raw traversal, immediately after computing it.
2. Separately: even after removing the wasted recomputation, a *correct*,
   fully deduplicated `three_hop` result on this dataset can legitimately
   exceed 1 MB. MovieLens has a strong "small-world" property â€” even a
   sparse user (20 ratings) has a 3-hop reach covering nearly the entire
   1,682-movie catalog (confirmed: user 926 correctly returns all 1,682
   movies, a 1,162,895-byte response). The `surrealdb` Python client
   inherits the `websockets` library's 1 MiB default incoming-message
   limit with no public way to configure it, so
   `benchmark/adapters/surrealdb_adapter.py` patches the underlying
   `connect()` call once at import time to set `max_size=None`. This
   changes no resource limit, dataset, workload definition, or round-trip
   count â€” `three_hop` is still one query per call, exactly as before.

**Why RocksDB:** the in-memory backend's OOM was initially (and reasonably)
suspected to be a data-residency problem, so the storage backend was
switched to RocksDB (persistent, disk-backed) as a first fix attempt. The
OOM recurred identically under RocksDB, which is itself informative: it
proved the crash was a *transient per-query memory spike* during a specific
`three_hop` call, not a static data-residency problem â€” switching backends
alone could never have fixed it. RocksDB was kept as the final storage
backend regardless (it is the more production-representative choice), but
the actual fix required the query-level dedup correction and the client-side
message-size patch together.

**Verified via:** contract tests, a live correctness smoke test with exact
hand-derived expected values, a targeted stress test against the real
dataset across 6 representative users (most active, sparsest, and raters of
the single most popular movie), with container memory monitored throughout
(peak: 265.5 MiB / 512 MiB), and a full clean benchmark re-run completing
with zero errors. Full incident writeup: `docs/known-issues.md`.

## 14. Reproducibility

```bash
# 1. Clone and enter the repo
git clone https://github.com/saijignas/cognodb-benchmark.git
cd cognodb-benchmark

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials (see Section 15) -- copy and fill in
cp .env.example .env

# 4. Download and validate the dataset
python3 scripts/preprocess_movielens.py
python3 scripts/validate_dataset.py

# 5. Run the contract and correctness tests
python3 -m pytest tests/test_adapter_contract.py -v
python3 scripts/smoke_test.py <platform>   # cognodb | neo4j | memgraph | arangodb | surrealdb

# 6. Run the full benchmark against one platform
python3 scripts/run_benchmark.py <platform> --dataset data/movielens_100k.json \
    --warmup 20 --iterations 100 --concurrency 10 --concurrent-seconds 30

# 7. Analyze results
python3 scripts/analyze_results.py
```

Local platforms need a running container first, e.g. for Neo4j:

```bash
docker run -d --name neo4j-bench --cpus=0.5 --memory=512m --memory-swap=512m \
  -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> \
  -e NEO4J_server_memory_heap_initial__size=128m \
  -e NEO4J_server_memory_heap_max__size=192m \
  -e NEO4J_server_memory_pagecache_size=64m \
  neo4j:5-community
```

See `docs/known-issues.md` for the equivalent commands for Memgraph,
ArangoDB, and SurrealDB (the latter using `rocksdb:/data/surreal.db` as its
storage argument, not the default `memory`).

The dataset is intentionally **not committed** (`data/` is gitignored) â€” it
is always regenerated deterministically from the canonical GroupLens source
by `scripts/preprocess_movielens.py`, so it can never silently drift from
that source of truth.

## 15. Environment Variables / Secrets

All credentials are read exclusively from environment variables (see
`benchmark/core/config.py`); none are ever printed, logged, or committed.
Copy `.env.example` to `.env` and fill in real values, or (as used in this
benchmark's actual execution) set them as GitHub Codespaces repository
secrets, which are injected automatically:

```
COGNODB_URI=
COGNODB_USER=
COGNODB_PASSWORD=

NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=

MEMGRAPH_URI=
MEMGRAPH_USER=
MEMGRAPH_PASSWORD=

ARANGO_URI=
ARANGO_USER=
ARANGO_PASSWORD=
ARANGO_DB=

SURREALDB_URI=ws://localhost:8000
SURREALDB_USER=
SURREALDB_PASSWORD=
SURREALDB_NS=
SURREALDB_DB=
```

`.gitignore` excludes `.env`, `data/`, `results/raw/*.jsonl`,
`results/logs/*.log`, `__pycache__/`, and `.pytest_cache/` â€” none of these
are ever committed.

## 16. Known Limitations

- **Storage parity is not hard-enforced for the four local platforms** â€”
  see Section 10 for the full disclosure and the evidence behind it.
- **CognoDB's absolute latency is confounded by network round-trip time**
  â€” see Section 11.
- **Resource footprint is only observable for ArangoDB** â€” the other four
  platforms expose no lightweight in-process introspection API that this
  benchmark uses; this is reported as `"not observable"` rather than
  estimated.
- **No architectural claim is made without direct measurement evidence.**
  Where a plausible explanation is offered (e.g. JVM GC pauses for Neo4j,
  in-memory-first design for Memgraph), it is explicitly flagged as an
  inference consistent with observed behavior, not a profiled, proven cause.
- **The concurrency-workload client itself has measured overhead**
  (process spawn, barrier synchronization) that is not zero â€” absolute
  throughput numbers include this client-side cost, most visible for
  CognoDB where the network path also adds latency per operation.

## 17. Final Conclusions

**Strongly supported by the data:**
- Memgraph is the fastest platform tested for ingest and the majority of
  latency workloads, with the tightest p50-to-p95 spread, under resource
  limits identical to every other platform.
- ArangoDB's `three_hop`, even after its correctness fix, is measurably the
  slowest `three_hop` among all local platforms.
- Neo4j cannot function at all under a 256 MiB memory ceiling â€” a hard,
  reproducible resource-requirement finding, independent of any query
  performance question.
- CognoDB's latency for simple lookups is dominated by network round-trip
  time rather than server-side execution cost.
- All five platforms were tested at a stated concurrency of 10 clients;
  all five runs completed with zero errors, though absolute throughput
  spans nearly an order of magnitude (CognoDB: 46.4 ops/s total vs.
  Memgraph: 448.0 ops/s total).

**Should NOT be claimed from this data:**
- That any platform's storage efficiency or storage-bound behavior is
  directly comparable â€” a genuine, matched storage quota could not be
  enforced locally (Section 10).
- That CognoDB's raw query-engine speed is definitively faster or slower
  than any specific local number â€” network latency is not separable from
  execution cost without CognoDB-side instrumentation this benchmark does
  not have (Section 11).
- Any specific internal architectural mechanism as the *proven* cause of a
  performance difference (e.g., asserting a specific GC algorithm, a
  specific storage-engine internal) â€” only externally observable,
  reproducible behavior is claimed; plausible explanations are labeled as
  such, not presented as verified fact.

