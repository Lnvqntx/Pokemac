"""Connect the desktop windows to engine actions and the animation clock."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from time import monotonic, time

from PySide6.QtCore import QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QMenu

from pokemac import engine
from pokemac.overlay.motion import Limits, Motion, SpawnSchedule
from pokemac.overlay.presentation import display_name, floats, load_artwork, progress_notice
from pokemac.overlay.tray import OverlayTray
from pokemac.overlay.widgets import (
    CollectionWindow,
    CompanionPanel,
    Message,
    SpriteWindow,
    sprite_pixmap,
)


def _read_state():
    return engine.get_status(), engine.list_tasks("pending"), engine.list_pokemon()


def _limits(area, window):
    left, top = area.x(), area.y()
    return Limits(
        left, top,
        max(left, left + area.width() - window.width()),
        max(top, top + area.height() - window.height()),
    )


@dataclass
class PartySprite:
    window: SpriteWindow
    motion: Motion
    pokemon: dict
    screen: object
    flash_pending: bool = False


class Overlay(QObject):
    def __init__(self, application, *, schedule=None, poll_interval_ms=3_000):
        super().__init__()
        self._application = application
        self._stopped = False
        self.companions = {}
        self.wild = SpriteWindow(wild=True)
        self.panel = CompanionPanel()
        self.collection = CollectionWindow()
        self.message = Message()
        # The tray borrows its context menu. The panel owns it on the Qt side.
        self.tray_menu = QMenu(self.panel)
        self.tray = OverlayTray(self, self.tray_menu)
        self.tray_actions = self.tray.actions
        self.tray_party_actions = self.tray.party_actions
        self._visible = True
        self._lead_id = None
        self._panel_id = None
        self._status = None
        self._tasks = []
        self._wild_species = None
        self._wild_id = None
        self._wild_expires_at = None
        self._wild_screen = None
        self._screens = set()
        self._schedule = SpawnSchedule() if schedule is None else schedule
        self._spawn_pending = False
        self._last_frame = monotonic()

        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pokemac-game")
        self._art_worker = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pokemac-art")
        self._poll_job = None
        self._action = None
        self._poll_failed = False
        self._art = {}
        self._pixmaps = {}
        self._art_jobs = {}
        self._art_retry_after = {}

        self.wild.clicked.connect(self._catch)
        self.panel.task_done.connect(self._complete_task)
        self.panel.quit_requested.connect(lambda: application.exit(0))
        self.tray.quit_requested.connect(lambda: application.exit(0))
        self.tray.visibility_requested.connect(self._set_visible)
        self.tray.lead_requested.connect(partial(self._change_party, engine.set_active_pokemon))
        self.tray.add_requested.connect(partial(self._change_party, engine.add_to_party))
        self.tray.remove_requested.connect(partial(self._change_party, engine.remove_from_party))
        self.tray.collection_requested.connect(self._show_collection)
        if self.tray.isSystemTrayAvailable():
            self.tray.show()
        self.wild.scale_changed.connect(self._rescale_sprites)
        application.screenAdded.connect(self._screen_added)
        application.screenRemoved.connect(self._screen_removed)
        for screen in application.screens():
            self._watch_screen(screen)
        self._fit_screens()

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
        if self._stopped:
            return
        self._stopped = True
        self._poll_timer.stop()
        self._frame_timer.stop()
        self._message_timer.stop()
        self._application.screenAdded.disconnect(self._screen_added)
        self._application.screenRemoved.disconnect(self._screen_removed)
        for screen in tuple(self._screens):
            self._unwatch_screen(screen)
        self.tray.blockSignals(True)
        self.tray.setContextMenu(None)
        self.tray.hide()
        self.tray_menu.hide()
        for member in self.companions.values():
            member.window.hide()
            member.window.deleteLater()
        for window in (self.wild, self.panel, self.collection, self.message):
            window.hide()
            window.deleteLater()
        self.tray.deleteLater()
        self._worker.shutdown(wait=False, cancel_futures=True)
        self._art_worker.shutdown(wait=False, cancel_futures=True)

    def _watch_screen(self, screen):
        self._screens.add(screen)
        screen.availableGeometryChanged.connect(self._fit_screens)
        screen.logicalDotsPerInchChanged.connect(self._rescale_sprites)

    def _unwatch_screen(self, screen):
        self._screens.remove(screen)
        screen.availableGeometryChanged.disconnect(self._fit_screens)
        screen.logicalDotsPerInchChanged.disconnect(self._rescale_sprites)

    def _screen_added(self, screen):
        self._watch_screen(screen)
        self._fit_screens()

    def _screen_removed(self, screen):
        self._unwatch_screen(screen)
        self._fit_screens()

    def _primary_screen(self):
        primary = self._application.primaryScreen()
        return primary if primary in self._screens else next(iter(self._screens), None)

    def _work_area(self, screen):
        geometry = screen.availableGeometry() if screen is not None else QRect(0, 0, 800, 600)
        return geometry.adjusted(6, 6, -6, -6)

    def _screen_at(self, x, y):
        screen = self._application.screenAt(QPoint(round(x), round(y)))
        return screen if screen in self._screens else None

    def _nearest_screen(self, window):
        center = window.geometry().center()
        screen = self._screen_at(center.x(), center.y())
        if screen is not None:
            return screen
        return min(self._screens, key=lambda screen: _limits(
            self._work_area(screen), window,
        ).distance_squared(window.x(), window.y()), default=None)

    def _fit_screens(self):
        for member in self.companions.values():
            if member.screen not in self._screens:
                member.screen = self._nearest_screen(member.window)
            member.motion.set_limits(_limits(self._work_area(member.screen), member.window))
            self._move_companion(member)
        if self._wild_screen not in self._screens:
            self._place_wild()
        else:
            limits = _limits(self._work_area(self._wild_screen), self.wild)
            self.wild.move(*limits.clamp(self.wild.x(), self.wild.y()))
        if self.panel.isVisible():
            self._place_above(self.panel, self.companions[self._panel_id].window)
        if self.collection.isVisible():
            self._place_collection()
        self._rescale_sprites()

    def _move_companion(self, member):
        member.window.face(member.motion.direction)
        member.window.set_napping(member.motion.napping)
        member.window.move(round(member.motion.x), round(member.motion.y))

    def _place_wild(self):
        lead = self.companions.get(self._lead_id)
        self._wild_screen = lead.screen if lead is not None else self._primary_screen()
        area = self._work_area(self._wild_screen)
        limits = _limits(area, self.wild)
        x = lead.motion.x if lead is not None else area.center().x()
        self.wild.move(*limits.opposite_edge(x))
        self.wild.face(1 if self.wild.x() == limits.left else -1)

    def _place_above(self, window, anchor):
        limits = _limits(self._work_area(self._nearest_screen(anchor)), window)
        x = anchor.x() + (anchor.width() - window.width()) // 2
        y = anchor.y() - window.height() - 8
        if y < limits.top:
            y = anchor.y() + anchor.height() + 8
        window.move(*limits.clamp(x, y))

    def _frame(self):
        now = monotonic()
        seconds = min(now - self._last_frame, 0.1)
        self._last_frame = now
        self._finish_work()
        if self._wild_expires_at is not None and time() >= self._wild_expires_at:
            self._set_wild(None, None, None)
        if self._visible:
            for pokemon_id, member in self.companions.items():
                panel_open = self.panel.isVisible() and self._panel_id == pokemon_id
                if not panel_open and not member.window.pressed:
                    member.motion.advance(seconds)
                    self._move_companion(member)

        if self._schedule.due():
            self._spawn_pending = True
        if self._spawn_pending and self._action is None:
            self._spawn_pending = False
            self._start_action(engine.spawn_wild, None, self.wild)

    def _set_visible(self, visible):
        self._visible = visible
        self.tray.set_overlay_visible(visible)
        for member in self.companions.values():
            self._show_sprite(member.window, member.pokemon["species"])
            if not visible:
                member.flash_pending = False
        self._show_sprite(self.wild, self._wild_species)
        if not visible:
            self.panel.hide()
            self.collection.hide()
            self.message.hide()

    def _toggle_panel(self, pokemon_id):
        if self.panel.isVisible() and self._panel_id == pokemon_id:
            self.panel.hide()
        else:
            self._panel_id = pokemon_id
            self.panel.reset_recipient()
            self._refresh_panel()
            self.panel.show()
            self.refresh()

    def _refresh_panel(self):
        member = self.companions[self._panel_id]
        self.panel.update_state(self._status, self._tasks, member.pokemon)
        self._place_above(self.panel, member.window)

    def _show_collection(self):
        self._place_collection()
        self.collection.show()
        self.refresh()

    def _place_collection(self):
        limits = _limits(self._work_area(self._primary_screen()), self.collection)
        self.collection.move(
            (limits.left + limits.right) // 2, (limits.top + limits.bottom) // 2,
        )

    def _start_drag(self, pokemon_id):
        if self._panel_id == pokemon_id:
            self.panel.hide()
        self.companions[pokemon_id].motion.begin_drag()

    def _drag(self, pokemon_id, x, y):
        member = self.companions[pokemon_id]
        screen = self._screen_at(x + member.window.width() / 2, y + member.window.height() / 2)
        if screen is not None and screen != member.screen:
            member.screen = screen
            member.motion.set_limits(_limits(self._work_area(screen), member.window))
        member.motion.drag_to(x, y)
        self._move_companion(member)

    def _notification_anchor(self):
        if self.panel.isVisible():
            return self.panel
        lead = self.companions.get(self._lead_id)
        return lead.window if lead is not None else self.wild

    def refresh(self):
        if not self._stopped and self._poll_job is None and self._action is None:
            self._poll_job = self._worker.submit(_read_state)

    def _start_action(self, operation, on_success, anchor):
        if self._action is not None:
            return
        # A snapshot started before the write may already be stale.
        if self._poll_job is not None:
            self._poll_job.cancel()
            self._poll_job = None
        self.panel.set_busy(True)
        self.tray.set_busy(True)
        self._action = self._worker.submit(operation), on_success, anchor

    def _change_party(self, operation, pokemon_id):
        self._start_action(partial(operation, pokemon_id), None, self._notification_anchor())

    def _complete_task(self, task_id, pokemon_id):
        self._start_action(
            partial(engine.complete_task, task_id, pokemon_id=pokemon_id),
            self._task_completed, self._notification_anchor(),
        )

    def _task_completed(self, result):
        awards = result["xp_awards"]
        for award in awards:
            pokemon = award["pokemon"]
            member = self.companions.get(pokemon["id"])
            if member is not None and progress_notice(member.pokemon, pokemon) is not None:
                return  # The refreshed snapshot will announce and animate the change.
        xp = result["task"]["xp_reward"]
        if len(awards) > 1:
            xp_text = " · ".join(
                f"{award['pokemon']['nickname'] or display_name(award['pokemon']['species'])}"
                f" +{award['xp_awarded']} XP" for award in awards
            )
        else:
            xp_text = f"+{xp} XP"
        balls = result["balls_awarded"]
        ball_name = "Poké Ball" if balls == 1 else "Poké Balls"
        self._notify(f"Task complete · {xp_text} · +{balls} {ball_name}", self._notification_anchor())

    def _catch(self):
        if self._wild_id is not None:
            self._start_action(
                partial(engine.try_catch, expected_encounter_id=self._wild_id),
                self._caught, self.wild,
            )

    def _caught(self, result):
        species = display_name(result["species"])
        text = f"Caught {species}!" if result["caught"] else f"{species} broke free."
        self._set_wild(None, None, None)
        self._notify(text, self.wild)

    def _notify(self, text, anchor):
        if not self._visible:
            return
        self.message.set_message(text)
        self._place_above(self.message, anchor)
        self.message.show()
        self._message_timer.start(3_200)

    def _finish_work(self):
        if self._action is not None and self._action[0].done():
            job, on_success, anchor = self._action
            self._action = None
            self.panel.set_busy(False)
            self.tray.set_busy(False)
            try:
                result = job.result()
            except Exception as error:
                self._notify(str(error), anchor)
            else:
                if on_success is not None:
                    on_success(result)
            self.refresh()

        if self._poll_job is not None and self._poll_job.done():
            job, self._poll_job = self._poll_job, None
            try:
                status, tasks, collection = job.result()
            except Exception as error:
                if not self._poll_failed:
                    self._notify(str(error), self._notification_anchor())
                self._poll_failed = True
            else:
                self._poll_failed = False
                self._apply_state(status, tasks, collection)

        for species, job in tuple(self._art_jobs.items()):
            if job.done():
                del self._art_jobs[species]
                try:
                    self._art[species] = job.result()
                except Exception:
                    self._art_retry_after[species] = monotonic() + 30
                else:
                    self._art_retry_after.pop(species, None)
                    if species == self._wild_species:
                        self._update_sprite(self.wild, species)
                for member in self.companions.values():
                    if species == member.pokemon["species"]:
                        if species in self._art:
                            self._update_sprite(member.window, species)
                        self._finish_flash(member)

    def _apply_state(self, status, tasks, collection):
        self._status, self._tasks = status, tasks
        party = status["party"]
        self._lead_id = party[0]["id"] if party else None
        notices = self._sync_party(party)
        if self.panel.isVisible():
            self._refresh_panel()
        self.tray.update_party(collection, party)
        self.collection.update_collection(collection, party)
        if notices:
            self._notify("\n".join(notices), self._notification_anchor())

        self._set_wild(status["wild_species"], status["wild_encounter_id"], status["wild_expires_at"])
        self._request_art(self._wild_species)

    def _set_wild(self, species, encounter_id, expires_at):
        if expires_at is not None and time() >= expires_at:
            species = encounter_id = expires_at = None
        if encounter_id != self._wild_id:
            self._wild_species = species
            self._wild_id = encounter_id
            self._wild_expires_at = expires_at
            if species is not None:
                self._place_wild()
            self._show_sprite(self.wild, species)

    def _sync_party(self, party):
        member_ids = {pokemon["id"] for pokemon in party}
        for pokemon_id in self.companions.keys() - member_ids:
            if self._panel_id == pokemon_id:
                self.panel.hide()
                self._panel_id = None
            window = self.companions.pop(pokemon_id).window
            window.hide()
            window.deleteLater()

        notices = []
        for pokemon in party:
            pokemon_id = pokemon["id"]
            species = pokemon["species"]
            member = self.companions.get(pokemon_id)
            previous = None if member is None else member.pokemon
            if member is None:
                member = self._add_companion(pokemon)
            member.pokemon = pokemon
            if previous is None or previous["species"] != species:
                member.flash_pending = False
                member.motion.set_floating(floats(species))
                self._show_sprite(member.window, species)
            self._request_art(species)
            notice = progress_notice(previous, pokemon)
            if notice is not None and self._visible:
                notices.append(notice.message)
                if notice.evolved:
                    member.flash_pending = True
                    if species not in self._art_jobs:
                        self._finish_flash(member)
        return notices

    def _add_companion(self, pokemon):
        pokemon_id = pokemon["id"]
        window = SpriteWindow()
        column, row = (pokemon["party_slot"] - 1) % 3, (pokemon["party_slot"] - 1) // 3
        screen = self._primary_screen()
        motion = Motion(
            _limits(self._work_area(screen), window), start=(0.18 + 0.32 * column, 0.24 + 0.48 * row),
            floating=floats(pokemon["species"]),
        )
        member = PartySprite(window, motion, pokemon, screen)
        self.companions[pokemon_id] = member
        window.clicked.connect(partial(self._toggle_panel, pokemon_id))
        window.drag_started.connect(partial(self._start_drag, pokemon_id))
        window.drag_moved.connect(partial(self._drag, pokemon_id))
        window.drag_released.connect(motion.end_drag)
        window.scale_changed.connect(self._rescale_sprites)
        self._move_companion(member)
        return member

    def _show_sprite(self, window, species):
        if species is None or not self._visible:
            window.hide()
        else:
            self._update_sprite(window, species)
            window.show()

    def _update_sprite(self, window, species):
        ratio = window.devicePixelRatioF()
        if not window.isVisible():
            screen = self._nearest_screen(window)
            if screen is not None:
                ratio = screen.devicePixelRatio()
        key = species, ratio
        if key not in self._pixmaps and species in self._art:
            image, smooth = self._art[species]
            self._pixmaps[key] = sprite_pixmap(image, ratio=ratio, smooth=smooth)
        window.set_species(species, self._pixmaps.get(key, QPixmap()))

    def _rescale_sprites(self):
        for member in self.companions.values():
            self._update_sprite(member.window, member.pokemon["species"])
        if self._wild_species is not None:
            self._update_sprite(self.wild, self._wild_species)

    def _finish_flash(self, member):
        if member.flash_pending:
            member.flash_pending = False
            member.window.flash()

    def _request_art(self, species):
        if species is None or species in self._art or species in self._art_jobs:
            return
        if monotonic() >= self._art_retry_after.get(species, 0):
            self._art_jobs[species] = self._art_worker.submit(load_artwork, species)
