# Known issues and incidents

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

