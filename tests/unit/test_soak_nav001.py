from tools.soak_nav001 import SoakTarget, numeric, target_cycle


def test_target_cycle_is_bounded_and_has_altitude_variation():
    targets = target_cycle(5)
    assert len(targets) == 8
    assert {t.altitude for t in targets} == {2.0, 3.0}
    for target in targets:
        assert -10 <= target.north <= 10
        assert -10 <= target.east <= 10
        n, e, d = target.ned()
        assert (n, e) == (target.north, target.east)
        assert d <= 0


def test_soak_target_ned_and_numeric_helpers():
    assert SoakTarget(1, 2, 3).ned() == [1, 2, -3]
    assert numeric("1.25") == 1.25
    assert numeric(None) is None
