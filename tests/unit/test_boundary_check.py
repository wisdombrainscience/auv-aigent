import unittest
from pathlib import Path

from orchestration.boundary_check import check_changed_files


POLICY = Path("config/boundaries.yaml")


class BoundaryTests(unittest.TestCase):
    def test_allowed_implementation_change(self):
        r = check_changed_files("implementation", ["src/capabilities/navigation/go_to_target.py"], POLICY)
        self.assertTrue(r.passed)

    def test_forbidden_spec_change(self):
        r = check_changed_files("implementation", ["specs/NAV-001.yaml"], POLICY)
        self.assertFalse(r.passed)
        self.assertIn("FORBIDDEN:specs/NAV-001.yaml", r.violations)

    def test_forbidden_verifier_change(self):
        r = check_changed_files("recovery", ["src/verification/goto_target_verifier.py"], POLICY)
        self.assertFalse(r.passed)


if __name__ == "__main__":
    unittest.main()
