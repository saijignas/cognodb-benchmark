from abc import ABC, abstractmethod


class GraphDBAdapter(ABC):
    """Common interface every platform adapter must implement.

    Logical workload definitions live here as docstrings; platform-specific
    query syntax lives entirely inside each adapter implementation.
    """

    name: str = "unknown"

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def health_check(self) -> bool: ...

    @abstractmethod
    def reset(self) -> None:
        """Remove all benchmark data so load_dataset starts from empty."""

    @abstractmethod
    def prepare_schema(self) -> None: ...

    @abstractmethod
    def prepare_indexes(self) -> None: ...

    @abstractmethod
    def load_dataset(self, users, movies, genres, ratings, genre_edges) -> dict:
        """Bulk-load the canonical MovieLens-100K dataset.

        Returns a dict with load_start, load_end, wall_clock_seconds,
        nodes_loaded, relationships_loaded.
        """

    @abstractmethod
    def warmup(self, iterations: int) -> None: ...

    @abstractmethod
    def one_hop(self, start_user_id: int) -> list:
        """Given a start user, return movies reachable in exactly one hop (RATED)."""

    @abstractmethod
    def two_hop(self, start_user_id: int) -> list:
        """Other users, excluding the start user, who rated at least one
        movie the start user also rated ("co-raters").

        The start user must be excluded by an explicit condition in every
        adapter's query (e.g. `WHERE u2.id <> $id`), never left to a query
        language's incidental relationship/edge-uniqueness behavior --
        Cypher happens to drop some self-matches as a side effect of its
        per-pattern relationship-uniqueness rule, but that behavior is
        edge-reuse-dependent and not equivalent to this definition, so it
        must not be relied upon.
        """

    @abstractmethod
    def three_hop(self, start_user_id: int) -> list:
        """Movies rated by any user in two_hop(start_user_id)'s result set.

        Defined purely as an expansion of that user set -- independent of
        which specific edge was used to identify a co-rater. This may
        legitimately include a movie the start user has already rated, if
        a co-rater also rated it; that is not deduplicated away.

        Every adapter must compute this as two explicit stages -- (1) the
        same co-rater set as two_hop, (2) movies rated by that set -- so
        the result is identical in shape across query languages regardless
        of a language's relationship/edge-uniqueness semantics. A single
        flat multi-hop pattern must not be used if the query language's
        pattern-matching semantics could silently drop results through
        edge reuse (this is exactly what a naive single-MATCH Cypher
        pattern does, empirically confirmed during adapter development).
        """

    @abstractmethod
    def point_lookup(self, user_id: int) -> dict:
        """Fetch a single user node by its unique id."""

    @abstractmethod
    def indexed_lookup(self, movie_title: str) -> list:
        """Fetch movies by an indexed, non-key property (title)."""

    @abstractmethod
    def aggregation(self) -> list:
        """Count ratings grouped by genre."""

    @abstractmethod
    def mixed_write(self, user_id: int, movie_id: int, rating: float) -> None:
        """Insert one new rating edge, used by the concurrent read/write workload."""

    @abstractmethod
    def get_footprint(self) -> dict:
        """Return whatever storage/memory footprint the platform exposes.

        Any field that cannot be observed must be set to the string
        'not observable' rather than omitted or guessed.
        """

