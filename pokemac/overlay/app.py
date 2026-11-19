"""Connect the desktop windows to engine actions and the animation clock."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from time import monotonic

from PySide6.QtCore import QObject, QRect, QTimer
from PySide6.QtGui import QPixmap

from pokemac import engine
from pokemac.core.sprites import get_sprite
from pokemac.overlay.motion import Limits, Motion, SpawnSchedule
from pokemac.overlay.widgets import (
    CompanionPanel,
    Message,
    SpriteWindow,
    display_name,
    sprite_pixmap,
)


def _read_state():
    return engine.get_status(), engine.list_tasks("pending")


def _limits(area, window):
    left, top = area.x(), area.y()
    return Limits(
        left, top,
        max(left, left + area.width() - window.width()),
        max(top, top + area.height() - window.height()),
    )


class Overlay(QObject):
    def __init__(self, application, *, schedule=None, poll_interval_ms=3_000):
        super().__init__()
        self.companion = SpriteWindow()
        self.wild = SpriteWindow(wild=True)
        self.panel = CompanionPanel()
        self.message = Message()
        self._companion_species = None
        self._wild_species = None
        self._screen = None
        self._area = QRect(0, 0, 800, 600)
        self._motion = Motion(_limits(self._area, self.companion))
        self._schedule = SpawnSchedule() if schedule is None else schedule
        self._spawn_pending = False
        self._last_frame = monotonic()

        self._worker = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pokemac")
        self._poll_job = None
        self._action = None
        self._poll_failed = False
        self._art = {}
        self._art_jobs = {}
        self._art_retry_after = {}

        self.companion.clicked.connect(self._toggle_panel)
        self.companion.drag_started.connect(self._start_drag)
        self.companion.drag_moved.connect(self._drag)
        self.companion.drag_released.connect(self._motion.end_drag)
        self.wild.clicked.connect(self._catch)
        self.panel.task_done.connect(self._complete_task)
        self.panel.quit_requested.connect(application.quit)
        application.primaryScreenChanged.connect(self._watch_screen)
        self._watch_screen(application.primaryScreen())

        self._message_timer = QTimer(self, singleShot=True)
        self._message_timer.timeout.connect(self.message.hide)
        self._frame_timer = QTimer(self, interval=33)
        self._frame_timer.timeout.connect(self._frame)
        self._frame_timer.start()
        self._poll_timer = QTimer(self, interval=poll_interval_ms)
        self._poll_timer.timeout.connect(self.refresh)
        self._poll_timer.start()
        self.refresh()

    def stop(self):
        self._poll_timer.stop()
        self._frame_timer.stop()
        self._message_timer.stop()
        for window in (self.companion, self.wild, self.panel, self.message):
            window.hide()
        self._worker.shutdown(wait=False, cancel_futures=True)

    def _watch_screen(self, screen):
        if self._screen is not None:
            self._screen.availableGeometryChanged.disconnect(self._resize_area)
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._resize_area)
            self._resize_area(screen.availableGeometry())

    def _resize_area(self, geometry):
        self._area = geometry.adjusted(6, 0, -6, -6)
        self._motion.set_limits(_limits(self._area, self.companion))
        self._move_companion()
        self._place_wild()
        if self.panel.isVisible():
            self._place_above(self.panel, self.companion)

    def _move_companion(self):
        self.companion.face(self._motion.direction)
        self.companion.move(round(self._motion.x), round(self._motion.y))

    def _place_wild(self):
        limits = _limits(self._area, self.wild)
        self.wild.move(*limits.opposite_edge(self._motion.x))
        self.wild.face(1 if self.wild.x() == limits.left else -1)

    def _place_above(self, window, anchor):
        limits = _limits(self._area, window)
        x = anchor.x() + (anchor.width() - window.width()) // 2
        y = anchor.y() - window.height() - 8
        window.move(*limits.clamp(x, y))

    def _frame(self):
        now = monotonic()
        seconds = min(now - self._last_frame, 0.1)
        self._last_frame = now
        self._finish_work()
        if not self.panel.isVisible() and not self.companion.pressed:
            self._motion.advance(seconds)
            self._move_companion()

        if self._schedule.due():
            self._spawn_pending = True
        if self._spawn_pending and self._action is None:
            self._spawn_pending = False
            self._start_action(engine.spawn_wild, self._spawned, self.wild)

    def _toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.adjustSize()
            self._place_above(self.panel, self.companion)
            self.panel.show()
            self.refresh()

    def _start_drag(self):
        self.panel.hide()
        self._motion.begin_drag()

    def _drag(self, x, y):
        self._motion.drag_to(x, y)
        self._move_companion()

    def refresh(self):
        if self._poll_job is None and self._action is None:
            self._poll_job = self._worker.submit(_read_state)

    def _start_action(self, operation, on_success, anchor):
        if self._action is not None:
            return
        # A snapshot started before the write may already be stale.
        if self._poll_job is not None:
            self._poll_job.cancel()
            self._poll_job = None
        self.panel.set_busy(True)
        self._action = self._worker.submit(operation), on_success, anchor

    def _complete_task(self, task_id):
        self._start_action(
            partial(engine.complete_task, task_id), self._task_completed, self.companion,
        )

    def _task_completed(self, result):
        anchor = self.panel if self.panel.isVisible() else self.companion
        species = result["pokemon"]["species"]
        if species != result["previous_species"]:
            self._notify(f"Evolved into {display_name(species)}!", anchor)
        else:
            xp = result["task"]["xp_reward"]
            balls = result["balls_awarded"]
            ball_name = "Poké Ball" if balls == 1 else "Poké Balls"
            self._notify(f"Task complete · +{xp} XP · +{balls} {ball_name}", anchor)

    def _spawned(self, _species):
        self._place_wild()

    def _catch(self):
        if self._wild_species is not None:
            self._start_action(
                partial(engine.try_catch, expected_species=self._wild_species),
                self._caught, self.wild,
            )

    def _caught(self, result):
        species = display_name(result["species"])
        text = f"Caught {species}!" if result["caught"] else f"{species} broke free."
        self._wild_species = None
        self.wild.hide()
        self._notify(text, self.wild)

    def _notify(self, text, anchor):
        self.message.set_message(text)
        self._place_above(self.message, anchor)
        self.message.show()
        self._message_timer.start(3_200)

    def _finish_work(self):
        if self._action is not None and self._action[0].done():
            job, on_success, anchor = self._action
            self._action = None
            self.panel.set_busy(False)
            try:
                result = job.result()
            except Exception as error:
                self._notify(str(error), anchor)
            else:
                on_success(result)
            self.refresh()

        if self._poll_job is not None and self._poll_job.done():
            job, self._poll_job = self._poll_job, None
            try:
                status, tasks = job.result()
            except Exception as error:
                if not self._poll_failed:
                    self._notify(str(error), self.companion)
                self._poll_failed = True
            else:
                self._poll_failed = False
                self._apply_state(status, tasks)

        for species, job in tuple(self._art_jobs.items()):
            if job.done():
                del self._art_jobs[species]
                try:
                    self._art[species] = sprite_pixmap(job.result())
                except Exception:
                    self._art_retry_after[species] = monotonic() + 30
                    continue
                self._art_retry_after.pop(species, None)
                if species == self._companion_species:
                    self.companion.set_species(species, self._art[species])
                if species == self._wild_species:
                    self.wild.set_species(species, self._art[species])

    def _apply_state(self, status, tasks):
        self.panel.update_state(status, tasks)
        if self.panel.isVisible():
            self._place_above(self.panel, self.companion)

        active = status["active_pokemon"]
        species = None if active is None else active["species"]
        if species != self._companion_species:
            self._companion_species = species
            self._show_sprite(self.companion, species)
        self._request_art(species)

        species = status["wild_species"]
        if species != self._wild_species:
            self._wild_species = species
            self._place_wild()
            self._show_sprite(self.wild, species)
        self._request_art(species)

    def _show_sprite(self, window, species):
        if species is None:
            window.hide()
        else:
            window.set_species(species, self._art.get(species, QPixmap()))
            window.show()

    def _request_art(self, species):
        if species is None or species in self._art or species in self._art_jobs:
            return
        if monotonic() >= self._art_retry_after.get(species, 0):
            self._art_jobs[species] = self._worker.submit(get_sprite, species)
