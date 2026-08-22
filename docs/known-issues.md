# Known issues and incidents

## SurrealDB in-memory backend OOM under the real MovieLens dataset

**Symptom:** During the full MovieLens-100k benchmark run against
SurrealDB's default in-memory storage backend (`surreal start ...
memory`), `two_hop` completed successfully but every `three_hop` call
(100/100) and every `aggregation` call (100/100) failed, and all 8
concurrent-workload connection attempts failed
(`connection_errors: 8`).

**Root cause, confirmed via `docker inspect`:** the container had
exited with `Status=exited`, `OOMKilled=true`, `ExitCode=137` --
the SurrealDB server process itself was killed by the kernel OOM killer
partway through the run, right after `two_hop` completed and during
`three_hop`.

**Follow-up attempt (also failed, same root cause class):** re-ran
against a persistent RocksDB storage backend instead of in-memory
(`surreal start ... rocksdb:/tmp/surreal.db`), keeping the exact same
0.5 CPU / 512 MiB / 512 MiB swap caps, dataset, workload definitions,
iteration counts, and concurrency -- only the storage backend changed.
RocksDB started cleanly (idle baseline ~108 MiB/512 MiB, comfortable
headroom) and `ingest`/`point_lookup`/`indexed_lookup`/`one_hop`/
`two_hop` all completed successfully again. But the **exact same
failure recurred at the exact same point**: `three_hop` (100/100
errors), `aggregation` (100/100 errors), and all 8 concurrent
connections failed, with `docker inspect` again showing
`OOMKilled=true, ExitCode=137`.

**Refined understanding:** because the crash happens at the identical
workload stage regardless of storage backend, and the base dataset
plus several read workloads consistently fit comfortably in memory
first, this is not simply "the dataset doesn't fit in 512 MiB." It
points instead to **per-query transient memory usage during a specific
`three_hop` call** -- almost certainly when a randomly sampled start
user lands on a densely connected part of the real MovieLens graph (a
popular movie with many co-raters), causing the `array::distinct`
staging to materialize a very large intermediate array for that one
query. This is the same class of root cause as the ArangoDB
`three_hop` combinatorial blowup (a co-rater/movie fan-out that is
invisible on tiny synthetic data but severe on real, densely connected
data) -- but it manifests as memory exhaustion here rather than a CPU/
time hang, and switching storage backends does not address a
transient per-query memory spike the way it might address a static
data-residency problem.

**Disposition:** neither failed run's measurements (including the
partial `two_hop` successes that preceded each crash) are included in
the reported results. Both attempts' partial raw result files were
deleted. Per explicit instruction, the resource limit was not
increased to work around this; the benchmark was stopped after the
second failure to report evidence and await direction, rather than
attempting a third storage backend or other resource-limit change
unilaterally.

**Actual root cause (found after further investigation) and fix:**
two separate, independent bugs were compounding, both in `three_hop`:

1. **Missing deduplication of the co-rater set**, the same defect class
   as the ArangoDB adapter's query bug: `$co_raters` was built from a raw traversal
   (`->rated->movie<-rated<-user`) that was never deduplicated -- a
   co-rater sharing K movies with the start user appeared K times.
   `array::complement($co_raters, [$u])` only removes exact matches to
   `$u`; it does not deduplicate the rest. The final `FROM $others`
   stage then independently re-traversed and materialized that
   co-rater's full rated-movie list once per duplicate occurrence
   instead of once per unique co-rater. Fixed by wrapping the raw
   traversal in `array::distinct()` immediately, before `complement`:
   `LET $co_raters = array::distinct((SELECT VALUE
   ->rated->movie<-rated<-user FROM $u)[0]);`. This preserves the exact
   same final result set (the same UNION of movies), just without
   redundant recomputation for every duplicate co-rater occurrence.

2. **A client-side WebSocket message-size ceiling**, unrelated to
   memory: the `surrealdb` Python client's `WebsocketConnection.connect()`
   calls `websockets.asyncio.client.connect()` with no `max_size`
   override, so it inherits that library's 1 MiB (1,048,576 byte)
   default incoming-message limit. Even after fix #1 removed the
   wasted recomputation, the *correct, fully deduplicated* result for
   `three_hop` on real MovieLens data can legitimately exceed 1 MiB:
   MovieLens exhibits a strong "small world" property where even a
   sparse user's 3-hop reach covers nearly the entire movie catalog
   (confirmed empirically -- user 926, with only 20 ratings, correctly
   returns all 1,682 movies in the dataset, a 1,162,895-byte response).
   This is not a bug in the query, not a server-side resource
   constraint, and not something to fix by capping the result -- it is
   an artificial client-library networking ceiling. There is no public
   parameter on `SurrealDB()` to configure it, so
   `benchmark/adapters/surrealdb_adapter.py` patches the underlying
   `websockets.connect` call once at import time to set
   `max_size=None` (unbounded), removing the ceiling entirely rather
   than truncating any result. This changes no server resource limit,
   no dataset, no workload definition, no iteration count or
   concurrency setting, and adds no extra network round trip --
   `three_hop` is still one `query()` call from the client's
   perspective, exactly as before.

**Verification:** contract tests (10/10), the correctness smoke test on
a fresh RocksDB-backed container (10/10, exact hand-derived
two_hop/three_hop values), and a targeted stress test against the real
MovieLens dataset for 6 representative users -- the most active user
(405, 737 ratings), the sparsest user (926, 20 ratings), two users who
rated the single most popular movie in the dataset (movie 50, 583
raters), and two more mid-range users -- all completed `three_hop`
successfully in under 1 second each, returning 1654-1682 movies (out of
1682 total), with peak observed container memory of 265.5 MiB out of
the 512 MiB cap (51.85%), monitored via a `docker stats` poller running
throughout. Only after all of this passed was the full benchmark
re-run, completing in ~2.5 minutes with zero errors across every
workload.

**Disposition:** all three attempts' (in-memory OOM, RocksDB OOM) failed
measurements remain excluded from the final results; only the final,
fully corrected run's raw results are included. SurrealDB's storage
backend for the final methodology is recorded as **RocksDB**
(`rocksdb:/tmp/...`), not the default in-memory mode.

## ArangoDB three_hop combinatorial blowup (found and fixed during the real benchmark run)

**Symptom:** During the full MovieLens-100k benchmark run, the ArangoDB
`three_hop` workload hung indefinitely. The process was manually
terminated after ~90 minutes with no result.

**Initial diagnosis (later corrected):** Wall-clock time and CPU usage
alone suggested the process was still stuck in `load_dataset()`'s single
unbatched `rated.insert_many()` call (~100,000 documents in one HTTP
request). This was wrong, and was corrected by checking the raw
per-workload result files directly rather than inferring from process
state: `ingest`, `point_lookup`, `indexed_lookup`, `one_hop`, and
`two_hop` had all already completed and been persisted -- ingest itself
took 7.05 seconds and loaded the correct counts (2,644 nodes, 102,893
relationships). The process was actually stuck in `three_hop`, several
stages past ingest. Ingest batching was never the problem and was left
unchanged.

**Root cause:** The AQL `three_hop` query filtered out the start user
but never deduplicated the co-rater set before the final expansion:

```aql
FOR m IN OUTBOUND @start rated
  FOR u2 IN INBOUND m rated
    FILTER u2.id != @start_id
    FOR m2 IN OUTBOUND u2 rated
      RETURN DISTINCT m2.id
```

If a co-rater shared K movies with the start user, their full rated-movie
list was re-expanded K times instead of once. On the real, densely
connected MovieLens graph (popular movies can have hundreds of raters),
this produced a genuine combinatorial blowup under the benchmark's
0.5 CPU cap. The equivalent Cypher implementation avoided this by
explicitly staging the computation (`WITH DISTINCT u2` between two
`MATCH` clauses), and the SurrealDB implementation avoided it via
`array::distinct` staging -- the AQL version was the only one of the
three that computed the co-rater "set" without actually deduplicating
it, so it did not correctly implement the common workload contract
already specified in `benchmark/adapters/base.py`.

**Fix:** Staged the AQL query the same way -- compute and deduplicate
the co-rater set first, then expand to rated movies:

```aql
LET co_raters = (
  FOR m IN OUTBOUND @start rated
    FOR u2 IN INBOUND m rated
      FILTER u2.id != @start_id
      RETURN DISTINCT u2
)
FOR u2 IN co_raters
  FOR m2 IN OUTBOUND u2 rated
    RETURN DISTINCT m2.id
```

**Verification:** contract tests (10/10 pass), the generalized
correctness smoke test against a live container (10/10 pass, matching
the exact hand-derived expected values for two_hop/three_hop), and a
full clean re-run of the real benchmark (completed in ~7 minutes total,
zero errors across every workload; three_hop p50=2398.6ms,
p95=3854.0ms).

**Disposition:** The abandoned ~90-minute attempt's partial raw result
files were deleted and are not part of the reported results. Only the
corrected, complete run's raw results are included in
`results/raw/arangodb_*.jsonl`.

ArangoDB's `three_hop` remains the slowest of the four Cypher/AQL/
SurrealQL-based local platforms even after the fix (2.4s median vs.
Neo4j's 147ms and Memgraph's 76ms on the same query). This is a
measured observation, not a proven architectural claim -- no profiling
was done to determine the exact internal cause, and it should be
reported as "ArangoDB's three_hop is slower here" rather than asserted
to be caused by any specific AQL engine internal.

**No changes were made to:** ingestion/batching behavior, the 512 MiB
memory limit, the 0.5 CPU limit, workload definitions, iteration counts,
concurrency settings, or any other platform's adapter.

