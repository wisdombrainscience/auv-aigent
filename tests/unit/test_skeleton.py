import unittest

from src.core.errors import FailureCode
from src.core.registry import CapabilityRegistry
from src.core.result import CapabilityResult
from orchestration.state_machine import OrchestratorState


class SkeletonTests(unittest.TestCase):
    def test_failure_model_contains_required_code(self):
        self.assertEqual(FailureCode.INVALID_INPUT.value, "INVALID_INPUT")

    def test_result_serializes(self):
        r = CapabilityResult(task_id="T", status="created")
        self.assertEqual(r.to_dict()["task_id"], "T")

    def test_registry_constructs(self):
        registry = CapabilityRegistry()
        self.assertEqual(registry._items, {})

    def test_orchestrator_initial_state_exists(self):
        self.assertEqual(OrchestratorState.LOAD_SPEC.value, "LOAD_SPEC")


if __name__ == "__main__":
    unittest.main()
