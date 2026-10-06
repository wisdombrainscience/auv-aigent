from __future__ import annotations

import os
from pathlib import Path


def data_root(project: Path) -> Path:
    """Persistent runtime data root.

    Fresh deployments may set ``UAV_DATA_DIR`` so evidence survives replacing
    or upgrading the source directory. Existing installations keep the legacy
    ``<project>/runs`` behavior when the variable is unset.
    """
    project = Path(project).expanduser().resolve()
    configured = str(os.environ.get("UAV_DATA_DIR") or "").strip()
    if not configured:
        return project
    return Path(os.path.expandvars(configured)).expanduser().resolve()


def runs_root(project: Path) -> Path:
    root = data_root(project)
    # Legacy mode stores runs directly under the source tree. Persistent-data
    # mode uses <UAV_DATA_DIR>/runs so the directory can also host future state.
    project = Path(project).expanduser().resolve()
    if root == project:
        return project / "runs"
    return root / "runs"


def runtime_root(project: Path) -> Path:
    return runs_root(project) / ".runtime"
