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
        """Users who rated at least one movie in common with the start user."""

    @abstractmethod
    def three_hop(self, start_user_id: int) -> list:
        """Movies rated by users who share a movie in common with the start user."""

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
