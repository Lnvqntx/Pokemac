from unittest.mock import Mock

import pytest

from pokemac.overlay.motion import Limits, Motion, SpawnSchedule


def test_walk_stays_at_the_floor_and_reverses_at_both_edges():
    limits = Limits(-100, 40, 0, 400)
    motion = Motion(limits, rng=Mock(uniform=Mock(return_value=7.0)))
    initial_x = motion.x
    motion.advance((limits.right - motion.x) / motion.speed)
    assert motion.x > initial_x
    assert (motion.x, motion.y, motion.direction) == (limits.right, limits.floor, -1)

    motion.advance((limits.right - limits.left) / motion.speed)
    assert (motion.x, motion.y, motion.direction) == (limits.left, limits.floor, 1)


def test_walking_pauses_use_injected_random_durations():
    rng = Mock()
    rng.uniform.side_effect = [3.5, 1.25, 4.0]
    motion = Motion(Limits(0, 0, 2_000, 400), rng=rng)
    motion.advance(4)
    resting_x = motion.x
    motion.advance(0.75)
    assert motion.x == resting_x
    motion.advance(0.5)
    assert motion.x == pytest.approx(resting_x + 0.5 * motion.speed)
    assert [entry.args for entry in rng.uniform.call_args_list] == [
        (3.0, 7.0), (0.6, 1.8), (3.0, 7.0),
    ]


def test_drag_clamps_to_the_screen_and_falls_when_released():
    motion = Motion(Limits(20, 10, 500, 360))
    motion.begin_drag()
    motion.drag_to(9_999, -99)
    assert (motion.x, motion.y) == (500, 10)
    motion.advance(10)
    assert (motion.x, motion.y) == (500, 10)

    motion.end_drag()
    motion.advance(0.1)
    first_y = motion.y
    assert first_y == pytest.approx(15.5)
    motion.advance(0.1)
    assert motion.y - first_y > first_y - 10
    motion.advance(3)
    assert (motion.x, motion.y) == (500, 360)


def test_changing_work_area_keeps_a_walking_sprite_above_the_dock():
    motion = Motion(Limits(30, 20, 700, 560))
    motion.set_limits(Limits(50, 20, 55, 480))
    assert (motion.x, motion.y) == (55, 480)
    motion.set_limits(Limits(50, 20, 50, 440))
    motion.advance(5)
    assert (motion.x, motion.y) == (50, 440)


def test_wild_encounter_is_placed_at_the_farther_edge():
    bounds = Limits(-800, 40, -120, 460)
    assert bounds.opposite_edge(-790) == (-120, 460)
    assert bounds.opposite_edge(-130) == (-800, 460)


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
