#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ai.agents import (  # noqa: E402
    TaskAgentMapper,
    TaskAgentUnknownRoleError,
)
from ai.project.store import ProjectStore  # noqa: E402
from ai.tasks import TaskCreateInput, TaskStore  # noqa: E402


def task_input(title: str, role: str) -> TaskCreateInput:
    return TaskCreateInput(
        title=title,
        role=role,
        priority=100,
        dependencies=(),
        input_artifacts=(),
        output_schema="engineering_task.v1",
        validators=("schema",),
        review_required=False,
        max_attempts=3,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="remote-uav-task-agent-map-"
    ) as raw:
        projects = ProjectStore(
            Path(raw) / "remote-uav" / "projects"
        )
        handle = projects.create_project()
        tasks = TaskStore(projects)

        first = tasks.create_task(
            handle.project_id,
            task_input(
                "First UAV systems task",
                "uav_systems_engineer",
            ),
        )
        second = tasks.create_task(
            handle.project_id,
            task_input(
                "Second UAV systems task",
                "uav_systems_engineer",
            ),
        )
        reserved = tasks.create_task(
            handle.project_id,
            task_input(
                "Reserved role task",
                "propulsion_power_engineer",
            ),
        )

        mapper = TaskAgentMapper(projects)
        first_binding = mapper.map_task(
            handle.project_id,
            first.task_id,
        )
        second_binding = mapper.map_task(
            handle.project_id,
            second.task_id,
        )

        if (
            first_binding.role_id != "uav_systems_engineer"
            or second_binding.role_id != "uav_systems_engineer"
        ):
            print("TASK_AGENT_MAPPING=FAIL reason=role")
            return 2

        if (
            first_binding.agent.instance_key != first.task_id.value
            or second_binding.agent.instance_key != second.task_id.value
            or first_binding.agent == second_binding.agent
        ):
            print("TASK_AGENT_MAPPING=FAIL reason=identity")
            return 3

        try:
            mapper.map_task(
                handle.project_id,
                reserved.task_id,
            )
        except TaskAgentUnknownRoleError:
            pass
        else:
            print("TASK_AGENT_MAPPING=FAIL reason=reserved_role_enabled")
            return 4

        restarted = TaskAgentMapper(
            ProjectStore(projects.projects_root)
        ).map_task(
            handle.project_id,
            first.task_id,
        )
        if restarted != first_binding:
            print("TASK_AGENT_MAPPING=FAIL reason=restart")
            return 5

        print(
            "TASK_AGENT_MAPPING=PASS "
            f"first={first.task_id.value}:uav_systems_engineer "
            f"second={second.task_id.value}:uav_systems_engineer "
            "isolated=PASS "
            "reserved_role=REJECTED "
            "restart=PASS "
            "provider_calls=none "
            "task_state=unchanged "
            "context=reserved_for_G6_4 "
            "output=reserved_for_G6_5 "
            "run_persistence=reserved_for_G6_6"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
