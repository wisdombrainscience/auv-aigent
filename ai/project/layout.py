from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ai.contracts.ids import CanonicalId, IdKind


PROJECT_LAYOUT_CONTRACT_VERSION = "1.0"

# Frozen baseline section 11.  Keep this order stable for deterministic tests,
# documentation, and future migrations.
PROJECT_DATA_DIRECTORIES: tuple[str, ...] = (
    "requirements",
    "architecture",
    "propulsion",
    "power",
    "sensors",
    "px4",
    "missions",
    "software",
    "simulation",
    "tests",
    "tasks",
    "ai_runs",
    "evidence",
    "reports",
)

PROJECT_METADATA_FILENAME = "project.json"


class ProjectLayoutError(ValueError):
    """Raised when a project filesystem layout violates the S3.2 contract."""


def default_projects_root(*, home: Path | None = None) -> Path:
    """Return the frozen local-first projects root.

    No directory is created here. S3.2 keeps path calculation separate from
    filesystem mutation so later create/open logic can compose it transactionally.
    """

    if home is None:
        home = Path.home()
    if not isinstance(home, Path):
        home = Path(home)

    home = home.expanduser()
    if not home.is_absolute():
        raise ProjectLayoutError("home must resolve to an absolute path")

    return home / ".local" / "share" / "remote-uav" / "projects"


def _require_project_id(value: CanonicalId | str) -> CanonicalId:
    project_id = value if isinstance(value, CanonicalId) else CanonicalId.parse(value)
    if project_id.kind is not IdKind.PROJECT:
        raise ProjectLayoutError(
            f"project layout requires a project ID, got {project_id.kind.value}"
        )
    return project_id


def _reject_symlink(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ProjectLayoutError(f"{label} must not be a symbolic link: {path}")


@dataclass(frozen=True)
class ProjectLayout:
    """Deterministic filesystem paths for one canonical project.

    S3.2 owns only the filesystem layout. It does not allocate project IDs,
    insert SQLite project rows, write project.json, create RequirementSet data,
    or open a project session. Those responsibilities remain gated to later
    Phase-3 work packages.
    """

    projects_root: Path
    project_id: CanonicalId

    def __post_init__(self) -> None:
        root = self.projects_root
        if not isinstance(root, Path):
            root = Path(root)
            object.__setattr__(self, "projects_root", root)

        root = root.expanduser()
        if not root.is_absolute():
            raise ProjectLayoutError("projects_root must be an absolute path")
        object.__setattr__(self, "projects_root", root)

        project_id = _require_project_id(self.project_id)
        object.__setattr__(self, "project_id", project_id)

    @property
    def project_dir(self) -> Path:
        return self.projects_root / self.project_id.value

    @property
    def project_json(self) -> Path:
        return self.project_dir / PROJECT_METADATA_FILENAME

    @property
    def data_directories(self) -> Mapping[str, Path]:
        return MappingProxyType(
            {
                name: self.project_dir / name
                for name in PROJECT_DATA_DIRECTORIES
            }
        )

    def ensure_directories(self) -> None:
        """Create the fixed directory tree only.

        This method deliberately does not create `project.json`. S3.3
        create/open project will own metadata creation/open semantics.
        """

        _reject_symlink(self.projects_root, label="projects_root")
        self.projects_root.mkdir(parents=True, exist_ok=True)

        _reject_symlink(self.project_dir, label="project_dir")
        self.project_dir.mkdir(mode=0o700, exist_ok=True)

        for name, path in self.data_directories.items():
            _reject_symlink(path, label=f"project directory {name}")
            path.mkdir(mode=0o700, exist_ok=True)

    def validate_directories(self) -> None:
        """Fail closed unless every required S3.2 directory exists."""

        if not self.projects_root.exists():
            raise ProjectLayoutError("projects_root does not exist")
        if not self.projects_root.is_dir():
            raise ProjectLayoutError("projects_root is not a directory")
        _reject_symlink(self.projects_root, label="projects_root")

        if not self.project_dir.exists():
            raise ProjectLayoutError("project_dir does not exist")
        if not self.project_dir.is_dir():
            raise ProjectLayoutError("project_dir is not a directory")
        _reject_symlink(self.project_dir, label="project_dir")

        for name, path in self.data_directories.items():
            if not path.exists():
                raise ProjectLayoutError(
                    f"required project directory is missing: {name}"
                )
            if not path.is_dir():
                raise ProjectLayoutError(
                    f"required project path is not a directory: {name}"
                )
            _reject_symlink(path, label=f"project directory {name}")

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic descriptive snapshot; no filesystem mutation."""

        return {
            "project_id": self.project_id.value,
            "projects_root": str(self.projects_root),
            "project_dir": str(self.project_dir),
            "project_json": str(self.project_json),
            "directories": {
                name: str(path)
                for name, path in self.data_directories.items()
            },
        }


def build_project_layout(
    projects_root: Path | str,
    project_id: CanonicalId | str,
) -> ProjectLayout:
    return ProjectLayout(
        projects_root=Path(projects_root),
        project_id=_require_project_id(project_id),
    )
