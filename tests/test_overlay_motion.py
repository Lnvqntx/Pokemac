from math import pi
from random import Random
from unittest.mock import Mock

import pytest

from pokemac.overlay.motion import Limits, Motion, SpawnSchedule


def test_ground_motion_uses_both_axes_and_bounces_off_screen_edges():
    bounds = Limits(-100, -200, 0, 0)
    rng = Mock()
    rng.uniform.side_effect = [pi / 4, 4.0]
    motion = Motion(bounds, rng=rng, start=(0.9, 0.95))
    motion.advance(0.2)
    assert motion.x > -10 and motion.y > -10
    assert motion.direction == 1
    motion.advance(0.25)
    assert motion.direction == -1
    assert bounds.left < motion.x < bounds.right and bounds.top < motion.y < bounds.bottom


@pytest.mark.parametrize(('roll', 'duration', 'nap'), [(0.9, 1, False), (0.0, 8, True)])
def test_wandering_turns_after_injected_pauses_and_naps(roll, duration, nap):
    rng = Mock()
    rng.uniform.side_effect = [0, 2, duration, pi / 2, 3]
    rng.random.return_value = roll
    motion = Motion(Limits(0, 0, 2_000, 800), rng=rng)
    motion.advance(2)
    resting = motion.x, motion.y
    assert motion.napping is nap
    motion.advance(duration / 2)
    assert (motion.x, motion.y) == resting
    motion.advance(duration / 2 + 0.25)
    assert not motion.napping
    assert motion.x == pytest.approx(resting[0])
    assert motion.y > resting[1]


@pytest.mark.parametrize('floating', [False, True])
def test_drag_stays_in_bounds_and_roams_from_drop_without_falling(floating):
    motion = Motion(Limits(20, -300, 700, 360), rng=Random(5), floating=floating)
    motion.begin_drag()
    motion.drag_to(9_999, -999)
    assert (motion.x, motion.y) == (700, -300)
    motion.drag_to(300, 30)
    motion.advance(10)
    assert (motion.x, motion.y) == (300, 30)
    motion.end_drag()
    assert (motion.x, motion.y) == (300, 30)
    motion.advance(0.1)
    assert (motion.x, motion.y) != (300, 30)
    assert abs(motion.x - 300) < 7 and abs(motion.y - 30) < 7


def test_floating_bobs_continuously_and_remains_within_work_area():
    limits = Limits(-400, 10, 300, 300)
    motion = Motion(limits, rng=Mock(uniform=Mock(return_value=0)), floating=True)
    x, y = motion.x, motion.y
    motion.advance(0.5)
    assert motion.x == pytest.approx(x + 16)
    assert motion.y > y
    motion.advance(1)
    assert motion.y < y
    for _ in range(100):
        motion.advance(0.5)
        assert (motion.x, motion.y) == limits.clamp(motion.x, motion.y)
        assert not motion.napping


def test_tighter_work_area_clamps_on_one_axis_and_keeps_other_coordinate():
    motion = Motion(Limits(-800, 20, -100, 560), rng=Random(5))
    motion.begin_drag()
    motion.drag_to(-600, 190)
    motion.set_limits(Limits(20, 120, 20, 300))
    assert (motion.x, motion.y) == (20, 190)
    motion.end_drag()
    motion.advance(0.1)
    assert motion.x == 20
    assert 120 < motion.y < 300


def test_wild_encounter_is_placed_at_far_edge_and_center_height():
    bounds = Limits(-800, 40, -120, 460)
    assert bounds.opposite_edge(-790) == (-120, 250)
    assert bounds.opposite_edge(-130) == (-800, 250)
    assert bounds.distance_squared(-130, 250) == 0
    assert bounds.distance_squared(-110, 30) == 200


def test_spawn_schedule_waits_twenty_minutes_and_coalesces_missed_intervals():
    clock = Mock(return_value=100)
    schedule = SpawnSchedule(clock=clock)
    assert not schedule.due()
    clock.return_value = 1_299
    assert not schedule.due()
    clock.return_value = 1_300
    assert schedule.due()
    assert not schedule.due()
    clock.return_value = 5_000
    assert schedule.due()
    assert not schedule.due()
    clock.return_value = 6_200
    assert schedule.due()


def test_spawn_interval_can_be_shortened_for_tests():
    clock = Mock(return_value=0)
    schedule = SpawnSchedule(interval=2, clock=clock)
    clock.return_value = 1
    assert not schedule.due()
    clock.return_value = 2
    assert schedule.due()
