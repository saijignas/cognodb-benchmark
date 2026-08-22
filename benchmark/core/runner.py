import concurrent.futures
import multiprocessing
import random
import time

from .results import append_raw_result
from .timing import TimedRun


def _concurrent_worker(adapter_cls, users, movies, duration_seconds, barrier, worker_seed):
    """Runs in its own OS process, not a thread.

    Verified necessary, not a stylistic choice: the SurrealDB Python
    client's asyncio event loop is a process-wide singleton, and a second
    thread calling connect() while another thread's loop is running
    raises "RuntimeError: This event loop is already running" (confirmed
    empirically). Rather than special-case one platform's client library,
    every adapter's concurrent workers run as separate processes, which
    sidesteps thread-safety assumptions for all five client libraries
    uniformly and also more accurately models independent concurrent
    clients than threads sharing one process would.

    Must be a module-level function (not a closure) and take only
    picklable arguments, since ProcessPoolExecutor pickles the call.

    The initial connect() retries a few times with backoff: launching N
    worker processes that each open a fresh connection at nearly the same
    instant can transiently exceed what a 0.5-CPU-capped server can
    accept in that instant (confirmed empirically -- a lone connection
    never times out, but 4 simultaneous ones sometimes do). Any real
    concurrent client would retry a transient connection failure rather
    than give up immediately, so this is realistic client behavior, not
    a way of hiding a problem. If every retry fails, that is recorded
    honestly as a connection error rather than crashing the whole
    concurrent workload measurement for every other worker.

    The measured loop only starts once every worker has reached the
    shared barrier (i.e. every worker that could connect has finished
    connecting). Without this, process-spawn-plus-import-plus-connect
    overhead for N processes can exceed a short measurement window
    entirely -- confirmed empirically: a 3-second window produced zero
    completed operations because every worker was still connecting when
    its deadline (computed before any process was even spawned) passed.
    """
    rng = random.Random(worker_seed)
    adapter = adapter_cls()
    counts = {
        "reads": 0,
        "writes": 0,
        "read_errors": 0,
        "write_errors": 0,
        "connection_errors": 0,
    }

    connected = False
    for attempt in range(4):
        try:
            adapter.connect()
            connected = True
            break
        except Exception:
            if attempt < 3:
                time.sleep(0.5 * (2**attempt))

    if not connected:
        counts["connection_errors"] += 1
        try:
            barrier.wait(timeout=60)
        except Exception:
            pass  # don't let one failed worker deadlock the others
        return counts

    try:
        barrier.wait(timeout=60)
    except Exception:
        pass

    stop_at = time.monotonic() + duration_seconds
    try:
        while time.monotonic() < stop_at:
            if rng.random() < 0.8:
                try:
                    adapter.one_hop(rng.choice(users))
                    counts["reads"] += 1
                except Exception:
                    counts["read_errors"] += 1
            else:
                try:
                    movie = rng.choice(movies)
                    adapter.mixed_write(rng.choice(users), movie["id"], rng.randint(1, 5))
                    counts["writes"] += 1
                except Exception:
                    counts["write_errors"] += 1
    finally:
        adapter.disconnect()
    return counts


class BenchmarkRunner:
    """Adapter-agnostic orchestration of the full benchmark suite for one
    platform. All workload *definitions* live in the adapter (via
    GraphDBAdapter's methods); this class only handles warm-up, measured
    iteration counts, timing, concurrency, and raw result persistence --
    it must never contain platform-specific logic.

    Query arguments (which user id, which movie title) are sampled
    randomly from the actual dataset on every iteration, seeded
    deterministically for reproducibility, rather than hammering the same
    single id repeatedly -- a fixed id would risk measuring one
    platform's caching behavior for that specific id rather than
    representative lookup/traversal cost.
    """

    def __init__(
        self,
        adapter,
        dataset: dict,
        warmup_iterations: int = 20,
        measured_iterations: int = 100,
        concurrency: int = 8,
        concurrent_duration_seconds: int = 10,
        seed: int = 1337,
    ):
        self.adapter = adapter
        self.dataset = dataset
        self.warmup_iterations = warmup_iterations
        self.measured_iterations = measured_iterations
        self.concurrency = concurrency
        self.concurrent_duration_seconds = concurrent_duration_seconds
        self._rng = random.Random(seed)

    def run_ingest(self) -> dict:
        self.adapter.reset()
        self.adapter.prepare_schema()
        self.adapter.prepare_indexes()
        result = self.adapter.load_dataset(
            self.dataset["users"],
            self.dataset["movies"],
            self.dataset["genres"],
            self.dataset["ratings"],
            self.dataset["genre_edges"],
        )
        wall = result["wall_clock_seconds"]
        result["nodes_per_second"] = result["nodes_loaded"] / wall if wall > 0 else None
        result["relationships_per_second"] = (
            result["relationships_loaded"] / wall if wall > 0 else None
        )
        append_raw_result(self.adapter.name, "ingest", result)
        return result

    def _run_timed_workload(self, label: str, fn, arg_pool: list | None = None) -> dict:
        def invoke():
            if arg_pool:
                arg = arg_pool[self._rng.randrange(len(arg_pool))]
                return fn(arg)
            return fn()

        for _ in range(self.warmup_iterations):
            try:
                invoke()
            except Exception:
                pass  # warm-up failures are not measured or recorded as results

        timed = TimedRun(label=label)
        for _ in range(self.measured_iterations):
            start = time.perf_counter()
            try:
                invoke()
            except Exception as exc:
                timed.record_error(repr(exc))
                continue
            elapsed_ms = (time.perf_counter() - start) * 1000
            timed.record(elapsed_ms)

        summary = timed.summary()
        append_raw_result(self.adapter.name, label, summary)
        return summary

    def run_point_lookup(self) -> dict:
        return self._run_timed_workload(
            "point_lookup", self.adapter.point_lookup, self.dataset["users"]
        )

    def run_indexed_lookup(self) -> dict:
        titles = [m["title"] for m in self.dataset["movies"]]
        return self._run_timed_workload(
            "indexed_lookup", self.adapter.indexed_lookup, titles
        )

    def run_one_hop(self) -> dict:
        return self._run_timed_workload(
            "one_hop", self.adapter.one_hop, self.dataset["users"]
        )

    def run_two_hop(self) -> dict:
        return self._run_timed_workload(
            "two_hop", self.adapter.two_hop, self.dataset["users"]
        )

    def run_three_hop(self) -> dict:
        return self._run_timed_workload(
            "three_hop", self.adapter.three_hop, self.dataset["users"]
        )

    def run_aggregation(self) -> dict:
        return self._run_timed_workload("aggregation", self.adapter.aggregation)

    def run_footprint(self) -> dict:
        result = self.adapter.get_footprint()
        append_raw_result(self.adapter.name, "footprint", result)
        return result

    def run_concurrent_workload(self, adapter_cls) -> dict:
        """Concurrent read/write throughput at self.concurrency, for
        self.concurrent_duration_seconds. adapter_cls is the adapter
        class itself (e.g. SurrealDBAdapter) -- each worker process
        constructs and connects its own instance; see _concurrent_worker
        for why this uses processes rather than threads and why a shared
        barrier gates the start of the measured window. Must run after
        every single-threaded latency workload so its writes don't
        perturb their results.

        Mix: 80% reads (one_hop over a random user), 20% writes
        (mixed_write with a random user/movie/rating).

        The reported duration_seconds is the configured window every
        successfully-connected worker actually looped for, not the raw
        wall-clock time from pool submission to completion -- the latter
        would also include process-spawn and connection setup time,
        which is not part of what "concurrent throughput" is measuring.
        wall_clock_seconds_including_setup is kept alongside it for
        transparency about total elapsed time.
        """
        users = self.dataset["users"]
        movies = self.dataset["movies"]

        manager = multiprocessing.Manager()
        barrier = manager.Barrier(self.concurrency)

        wall_start = time.monotonic()
        counters = {
            "reads": 0,
            "writes": 0,
            "read_errors": 0,
            "write_errors": 0,
            "connection_errors": 0,
        }
        with concurrent.futures.ProcessPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [
                executor.submit(
                    _concurrent_worker,
                    adapter_cls,
                    users,
                    movies,
                    self.concurrent_duration_seconds,
                    barrier,
                    self._rng.randrange(1_000_000),
                )
                for _ in range(self.concurrency)
            ]
            for f in futures:
                local = f.result()
                for key in counters:
                    counters[key] += local[key]
        wall_elapsed = time.monotonic() - wall_start

        duration = self.concurrent_duration_seconds
        total_ops = counters["reads"] + counters["writes"]
        result = {
            "concurrency": self.concurrency,
            "duration_seconds": duration,
            "wall_clock_seconds_including_setup": wall_elapsed,
            "reads_completed": counters["reads"],
            "writes_completed": counters["writes"],
            "read_errors": counters["read_errors"],
            "write_errors": counters["write_errors"],
            "connection_errors": counters["connection_errors"],
            "reads_per_second": counters["reads"] / duration if duration > 0 else None,
            "writes_per_second": counters["writes"] / duration if duration > 0 else None,
            "total_ops_per_second": total_ops / duration if duration > 0 else None,
        }
        append_raw_result(self.adapter.name, "concurrent", result)
        return result

    def run_all(self, adapter_cls) -> dict:
        results = {}
        results["ingest"] = self.run_ingest()
        results["point_lookup"] = self.run_point_lookup()
        results["indexed_lookup"] = self.run_indexed_lookup()
        results["one_hop"] = self.run_one_hop()
        results["two_hop"] = self.run_two_hop()
        results["three_hop"] = self.run_three_hop()
        results["aggregation"] = self.run_aggregation()
        results["footprint"] = self.run_footprint()
        results["concurrent"] = self.run_concurrent_workload(adapter_cls)
        return results

