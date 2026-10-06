from __future__ import annotations

from pathlib import Path

import pytest

from ai.contracts.ids import CanonicalId, IdKind
from ai.project.layout import (
    PROJECT_DATA_DIRECTORIES,
    PROJECT_LAYOUT_CONTRACT_VERSION,
    PROJECT_METADATA_FILENAME,
    ProjectLayout,
    ProjectLayoutError,
    build_project_layout,
    default_projects_root,
)


EXPECTED_DIRS = (
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


def project_id() -> CanonicalId:
    return CanonicalId.from_sequence(IdKind.PROJECT, 1)


def test_contract_version_is_explicit() -> None:
    assert PROJECT_LAYOUT_CONTRACT_VERSION == "1.0"


def test_directory_contract_matches_frozen_baseline_exactly() -> None:
    assert PROJECT_DATA_DIRECTORIES == EXPECTED_DIRS
    assert len(PROJECT_DATA_DIRECTORIES) == 14
    assert len(set(PROJECT_DATA_DIRECTORIES)) == 14


def test_project_metadata_filename_is_reserved() -> None:
    assert PROJECT_METADATA_FILENAME == "project.json"


def test_default_projects_root_matches_frozen_baseline(tmp_path: Path) -> None:
    assert default_projects_root(home=tmp_path) == (
        tmp_path / ".local" / "share" / "remote-uav" / "projects"
    )


def test_default_root_calculation_has_no_side_effect(tmp_path: Path) -> None:
    root = default_projects_root(home=tmp_path)
    assert not root.exists()


def test_relative_projects_root_is_rejected() -> None:
    with pytest.raises(ProjectLayoutError, match="absolute"):
        ProjectLayout(
            projects_root=Path("relative/projects"),
            project_id=project_id(),
        )


@pytest.mark.parametrize(
    "bad",
    [
        "REQ-000001",
        "TASK-000001",
        "ART-000001",
        "AI-RUN-000001",
        "ISSUE-0001",
    ],
)
def test_non_project_id_is_rejected(bad: str) -> None:
    with pytest.raises(ProjectLayoutError, match="project ID"):
        build_project_layout(Path("/tmp/projects"), bad)


def test_project_path_is_derived_only_from_canonical_id(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    layout = ProjectLayout(root, project_id())
    assert layout.project_dir == root / "UAV-PROJ-000001"
    assert layout.project_json == root / "UAV-PROJ-000001" / "project.json"


def test_data_directory_mapping_is_deterministic(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    assert tuple(layout.data_directories.keys()) == EXPECTED_DIRS
    assert tuple(path.name for path in layout.data_directories.values()) == EXPECTED_DIRS


def test_data_directory_mapping_is_immutable(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    with pytest.raises(TypeError):
        layout.data_directories["new"] = tmp_path / "x"  # type: ignore[index]


def test_ensure_directories_creates_only_fixed_layout(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()

    assert layout.project_dir.is_dir()
    for path in layout.data_directories.values():
        assert path.is_dir()

    assert not layout.project_json.exists()
    assert sorted(p.name for p in layout.project_dir.iterdir()) == sorted(EXPECTED_DIRS)


def test_ensure_directories_is_idempotent(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()
    layout.ensure_directories()
    layout.validate_directories()


def test_validate_directories_survives_reconstruction_restart(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    first = ProjectLayout(root, project_id())
    first.ensure_directories()

    reopened = ProjectLayout(root, "UAV-PROJ-000001")
    reopened.validate_directories()
    assert reopened.to_dict() == first.to_dict()


def test_missing_directory_fails_closed(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()
    (layout.project_dir / "evidence").rmdir()

    with pytest.raises(ProjectLayoutError, match="missing: evidence"):
        layout.validate_directories()


def test_required_path_as_regular_file_fails_closed(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()
    target = layout.project_dir / "reports"
    target.rmdir()
    target.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ProjectLayoutError, match="not a directory: reports"):
        layout.validate_directories()


def test_project_dir_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    link = root / "UAV-PROJ-000001"
    link.symlink_to(external, target_is_directory=True)

    layout = ProjectLayout(root, project_id())
    with pytest.raises(ProjectLayoutError, match="symbolic link"):
        layout.ensure_directories()


def test_child_directory_symlink_is_rejected(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()

    target = layout.project_dir / "requirements"
    target.rmdir()
    external = tmp_path / "outside"
    external.mkdir()
    target.symlink_to(external, target_is_directory=True)

    with pytest.raises(ProjectLayoutError, match="symbolic link"):
        layout.validate_directories()


def test_layout_does_not_write_project_json(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()
    assert not layout.project_json.exists()


def test_layout_does_not_touch_sqlite(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    layout.ensure_directories()
    assert not list(layout.project_dir.rglob("*.sqlite"))
    assert not list(layout.project_dir.rglob("*.db"))


def test_to_dict_is_descriptive_and_stable(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    data = layout.to_dict()

    assert data["project_id"] == "UAV-PROJ-000001"
    assert data["project_dir"] == str(tmp_path / "projects" / "UAV-PROJ-000001")
    assert data["project_json"].endswith("/UAV-PROJ-000001/project.json")
    assert tuple(data["directories"].keys()) == EXPECTED_DIRS


def test_s3_2_does_not_allocate_ids(tmp_path: Path) -> None:
    layout = build_project_layout(
        tmp_path / "projects",
        "UAV-PROJ-000042",
    )
    assert layout.project_id.sequence == 42
    assert layout.project_id.value == "UAV-PROJ-000042"


def test_s3_2_has_fixed_bounded_directory_count(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "projects", project_id())
    assert len(layout.data_directories) == 14
