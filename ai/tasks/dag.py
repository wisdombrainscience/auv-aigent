from __future__ import annotations

from dataclasses import dataclass
import heapq
import sqlite3

from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import ProjectStore
from ai.tasks.dependencies import TaskDependencyConflictError, TaskDependencyStore
from ai.tasks.store import (
    TaskNotFoundError,
    TaskStore,
    _parse_id,
    _registry_path,
    _reject_registry_path,
    initialize_task_registry,
    verify_task_registry,
)


TASK_DAG_CONTRACT_VERSION = "1.0"

# Defensive project-registry scan bounds. They do not replace the frozen
# planner policy max_tasks_per_plan=30 or max_dependency_depth=12.
MAX_TASK_DAG_NODES = 4096
MAX_TASK_DAG_EDGES = MAX_TASK_DAG_NODES * 30


class TaskDAGError(RuntimeError):
    """Base error for deterministic T5.4 task DAG validation."""


class TaskDAGConflictError(TaskDAGError):
    """Raised when the graph cannot be validated safely."""


class TaskDAGCycleError(TaskDAGError):
    """Raised when the persisted task dependency graph contains a cycle."""

    def __init__(self, cycle: "TaskCycle") -> None:
        self.cycle = cycle
        rendered = " -> ".join(item.value for item in cycle.task_ids)
        super().__init__(f"task DAG contains cycle: {rendered}")


@dataclass(frozen=True)
class TaskCycle:
    """One deterministic cycle witness with repeated start node at the end."""

    task_ids: tuple[CanonicalId, ...]

    def __post_init__(self) -> None:
        raw = self.task_ids
        if not isinstance(raw, tuple):
            try:
                raw = tuple(raw)
            except TypeError as exc:
                raise TaskDAGConflictError(
                    "cycle task_ids must be iterable"
                ) from exc
            object.__setattr__(self, "task_ids", raw)

        if len(raw) < 2:
            raise TaskDAGConflictError(
                "cycle witness must contain at least start and repeated end"
            )

        parsed: list[CanonicalId] = []
        for value in raw:
            try:
                canonical = (
                    value
                    if isinstance(value, CanonicalId)
                    else CanonicalId.parse(value)
                )
            except (CanonicalIdError, TypeError) as exc:
                raise TaskDAGConflictError(
                    "cycle witness contains invalid task ID"
                ) from exc
            if canonical.kind is not IdKind.TASK:
                raise TaskDAGConflictError(
                    "cycle witness may contain task IDs only"
                )
            parsed.append(canonical)

        if parsed[0] != parsed[-1]:
            raise TaskDAGConflictError(
                "cycle witness must repeat its start task at the end"
            )
        object.__setattr__(self, "task_ids", tuple(parsed))


@dataclass(frozen=True)
class TaskDAGReport:
    project_id: CanonicalId
    node_count: int
    edge_count: int
    topological_order: tuple[CanonicalId, ...]

    def __post_init__(self) -> None:
        project = self.project_id
        if not isinstance(project, CanonicalId):
            try:
                project = CanonicalId.parse(project)
            except (CanonicalIdError, TypeError) as exc:
                raise TaskDAGConflictError(
                    "project_id must be canonical"
                ) from exc
            object.__setattr__(self, "project_id", project)

        if project.kind is not IdKind.PROJECT:
            raise TaskDAGConflictError(
                "project_id must identify a project"
            )
        if (
            isinstance(self.node_count, bool)
            or not isinstance(self.node_count, int)
            or self.node_count < 0
        ):
            raise TaskDAGConflictError("node_count is invalid")
        if (
            isinstance(self.edge_count, bool)
            or not isinstance(self.edge_count, int)
            or self.edge_count < 0
        ):
            raise TaskDAGConflictError("edge_count is invalid")
        if len(self.topological_order) != self.node_count:
            raise TaskDAGConflictError(
                "topological_order length does not match node_count"
            )


def _find_cycle(
    dependencies: dict[str, tuple[str, ...]],
) -> TaskCycle | None:
    """Find one stable cycle witness using iterative DFS."""

    WHITE = 0
    GRAY = 1
    BLACK = 2
    color = {node: WHITE for node in dependencies}

    for start in sorted(dependencies):
        if color[start] != WHITE:
            continue

        active: list[str] = [start]
        active_pos: dict[str, int] = {start: 0}
        color[start] = GRAY
        stack: list[tuple[str, int]] = [(start, 0)]

        while stack:
            node, next_index = stack[-1]
            outgoing = dependencies[node]

            if next_index >= len(outgoing):
                stack.pop()
                color[node] = BLACK
                active_pos.pop(node, None)
                if not active or active[-1] != node:
                    raise TaskDAGConflictError(
                        "internal DAG traversal stack inconsistency"
                    )
                active.pop()
                continue

            dependency = outgoing[next_index]
            stack[-1] = (node, next_index + 1)

            dep_color = color.get(dependency)
            if dep_color is None:
                raise TaskDAGConflictError(
                    "cycle scan encountered dependency outside project graph"
                )
            if dep_color == BLACK:
                continue
            if dep_color == GRAY:
                start_index = active_pos[dependency]
                witness = active[start_index:] + [dependency]
                return TaskCycle(
                    tuple(CanonicalId.parse(item) for item in witness)
                )

            color[dependency] = GRAY
            active_pos[dependency] = len(active)
            active.append(dependency)
            stack.append((dependency, 0))

    return None


class TaskDAGValidator:
    """Read-only deterministic T5.4 DAG validator.

    T5.4 verifies target existence through T5.3, detects cycles, and returns a
    stable topological order. It does not resolve READY/BLOCKED or schedule.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        if not isinstance(project_store, ProjectStore):
            raise TypeError("project_store must be ProjectStore")
        self._project_store = project_store
        self._tasks = TaskStore(project_store)
        self._dependencies = TaskDependencyStore(project_store)

    def _load_graph(
        self,
        project_id: CanonicalId | str,
    ) -> tuple[
        CanonicalId,
        dict[str, tuple[str, ...]],
        dict[str, tuple[str, ...]],
        int,
        bool,
    ]:
        handle = self._tasks._open_handle(project_id)
        path = _registry_path(handle)
        if not path.exists():
            return handle.project_id, {}, {}, 0, False

        _reject_registry_path(path)
        initialize_task_registry(path)

        with sqlite3.connect(path) as connection:
            verify_task_registry(connection)

            task_rows = connection.execute(
                """
                SELECT task_id, project_id
                FROM tasks
                ORDER BY task_id
                LIMIT ?
                """,
                (MAX_TASK_DAG_NODES + 1,),
            ).fetchall()
            if len(task_rows) > MAX_TASK_DAG_NODES:
                raise TaskDAGConflictError(
                    "task DAG node count exceeds MAX_TASK_DAG_NODES"
                )

            nodes: list[str] = []
            for task_id, project_id_raw in task_rows:
                task = _parse_id(
                    task_id,
                    kind=IdKind.TASK,
                    field="task_id",
                )
                if project_id_raw != handle.project_id.value:
                    raise TaskDAGConflictError(
                        "task registry contains task from another project"
                    )
                nodes.append(task.value)

            edge_count = connection.execute(
                "SELECT COUNT(*) FROM task_dependency_edges"
            ).fetchone()[0]
            if (
                isinstance(edge_count, bool)
                or not isinstance(edge_count, int)
                or edge_count < 0
            ):
                raise TaskDAGConflictError(
                    "task DAG edge count is invalid"
                )
            if edge_count > MAX_TASK_DAG_EDGES:
                raise TaskDAGConflictError(
                    "task DAG edge count exceeds MAX_TASK_DAG_EDGES"
                )

            edge_rows = connection.execute(
                """
                SELECT child_task_id, ordinal, dependency_task_id
                FROM task_dependency_edges
                ORDER BY child_task_id, ordinal
                """
            ).fetchall()

        node_set = set(nodes)
        dependencies_mutable: dict[str, list[str]] = {
            node: [] for node in nodes
        }
        dependents_mutable: dict[str, list[str]] = {
            node: [] for node in nodes
        }

        for child, _ordinal, dependency in edge_rows:
            if child not in node_set:
                raise TaskDAGConflictError(
                    "task dependency edge child does not exist in graph"
                )
            if dependency not in node_set:
                raise TaskDAGConflictError(
                    "task dependency target does not exist in project: "
                    f"{child} -> {dependency}"
                )
            dependencies_mutable[child].append(dependency)
            dependents_mutable[dependency].append(child)

        dependencies = {
            node: tuple(values)
            for node, values in dependencies_mutable.items()
        }
        dependents = {
            node: tuple(sorted(values))
            for node, values in dependents_mutable.items()
        }
        return (
            handle.project_id,
            dependencies,
            dependents,
            edge_count,
            True,
        )

    def detect_cycle(
        self,
        project_id: CanonicalId | str,
    ) -> TaskCycle | None:
        (
            _project,
            dependencies,
            _dependents,
            _edge_count,
            registry_exists,
        ) = self._load_graph(project_id)

        if not registry_exists:
            return None

        try:
            self._dependencies.verify_project(project_id)
        except (TaskDependencyConflictError, TaskNotFoundError) as exc:
            raise TaskDAGConflictError(str(exc)) from exc

        return _find_cycle(dependencies)

    def validate_project(
        self,
        project_id: CanonicalId | str,
    ) -> TaskDAGReport:
        (
            project,
            dependencies,
            dependents,
            edge_count,
            registry_exists,
        ) = self._load_graph(project_id)

        if registry_exists:
            try:
                self._dependencies.verify_project(project_id)
            except (TaskDependencyConflictError, TaskNotFoundError) as exc:
                raise TaskDAGConflictError(str(exc)) from exc

        cycle = _find_cycle(dependencies)
        if cycle is not None:
            raise TaskDAGCycleError(cycle)

        indegree = {
            node: len(dependencies[node])
            for node in dependencies
        }
        ready = [
            node
            for node, count in indegree.items()
            if count == 0
        ]
        heapq.heapify(ready)

        ordered: list[str] = []
        while ready:
            node = heapq.heappop(ready)
            ordered.append(node)
            for child in dependents[node]:
                indegree[child] -= 1
                if indegree[child] < 0:
                    raise TaskDAGConflictError(
                        "task DAG indegree became negative"
                    )
                if indegree[child] == 0:
                    heapq.heappush(ready, child)

        if len(ordered) != len(dependencies):
            raise TaskDAGConflictError(
                "task graph could not produce a complete topological order"
            )

        return TaskDAGReport(
            project_id=project,
            node_count=len(dependencies),
            edge_count=edge_count,
            topological_order=tuple(
                CanonicalId.parse(item)
                for item in ordered
            ),
        )
