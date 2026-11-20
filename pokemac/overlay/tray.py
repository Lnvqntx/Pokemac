"""Menu bar controls for the companion."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from pokemac.engine import PARTY_LIMIT
from pokemac.overlay.presentation import display_name


def _tray_icon():
    icon = QIcon()
    for ratio in (1, 2):
        pixmap = QPixmap(18 * ratio, 18 * ratio)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        with QPainter(pixmap) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.black)
            painter.drawEllipse(QRectF(4, 8.5, 10, 7.5))
            for x, y in ((1.5, 5.8), (5.3, 2.2), (9.3, 2.2), (13.1, 5.8)):
                painter.drawEllipse(QRectF(x, y, 3.3, 4.1))
        icon.addPixmap(pixmap)
    icon.setIsMask(True)
    return icon


class OverlayTray(QSystemTrayIcon):
    visibility_requested = Signal(bool)
    lead_requested = Signal(object)
    add_requested = Signal(object)
    remove_requested = Signal(object)
    collection_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent, menu):
        super().__init__(_tray_icon(), parent)
        self.setToolTip("Pokemac")
        self._overlay_visible = True
        self._choices = None
        toggle = menu.addAction("Hide overlay")
        toggle.triggered.connect(lambda: self.visibility_requested.emit(not self._overlay_visible))
        self._party = QMenu("Party", menu)
        party_action = menu.addMenu(self._party)
        self.party_menus = {
            key: QMenu(title, self._party) for key, title in (
                ("add", "Add to party"), ("remove", "Remove from party"), ("lead", "Make lead"),
            )
        }
        self.party_menu_actions = {
            key: self._party.addMenu(submenu) for key, submenu in self.party_menus.items()
        }
        self.party_actions = {key: {} for key in self.party_menus}
        self._party.setEnabled(False)
        collection = menu.addAction("Collection", lambda: self.collection_requested.emit())
        github = menu.addAction("GitHub · not connected")
        github.setEnabled(False)
        separator = menu.addSeparator()
        quit_action = menu.addAction("Quit", lambda: self.quit_requested.emit())
        self.actions = {
            "toggle": toggle, "party": party_action, "collection": collection,
            "github": github, "separator": separator, "quit": quit_action,
        }
        self.setContextMenu(menu)

    def set_overlay_visible(self, visible):
        self._overlay_visible = visible
        self.actions["toggle"].setText("Hide overlay" if visible else "Show overlay")

    def update_integration_status(self, state, error=None):
        action = self.actions["github"]
        action.setText(f"GitHub · {state}")
        action.setToolTip(error or "")

    def set_busy(self, busy):
        self._party.setEnabled(bool(self._choices and self._choices[0]) and not busy)

    def update_party(self, collection, party):
        member_ids = {pokemon["id"] for pokemon in party}
        add = [pokemon for pokemon in collection if pokemon["id"] not in member_ids]
        collection_choices = tuple(
            (pokemon["id"], pokemon["nickname"] or display_name(pokemon["species"]),
             pokemon["level"])
            for pokemon in collection
        )
        choices = collection_choices, tuple(pokemon["id"] for pokemon in party)
        if choices == self._choices:
            return
        self._choices = choices
        self._refresh_menu("add", add, self.add_requested)
        self.party_menus["add"].setEnabled(bool(add) and len(party) < PARTY_LIMIT)
        self._refresh_menu("remove", party, self.remove_requested)
        others = [pokemon for pokemon in party if not pokemon["is_active"]]
        self._refresh_menu("lead", others, self.lead_requested)
        self.set_busy(False)

    def _refresh_menu(self, key, entries, requested):
        menu = self.party_menus[key]
        previous = self.party_actions[key]
        ordered = {}
        menu.setEnabled(bool(entries))
        for pokemon in entries:
            pokemon_id = pokemon["id"]
            name = pokemon["nickname"] or display_name(pokemon["species"])
            suffix = " · Lead" if pokemon["is_active"] else ""
            action = previous.get(pokemon_id)
            if action is None:
                action = QAction(menu)
                action.triggered.connect(lambda _, selected=pokemon_id: requested.emit(selected))
            action.setText(f"{name} · Lv. {pokemon['level']} · #{pokemon_id}{suffix}")
            ordered[pokemon_id] = action

        # Native menu dispatch can still be using an action when a poll refreshes the roster.
        for pokemon_id in previous.keys() - ordered.keys():
            action = previous[pokemon_id]
            action.setEnabled(False)
            menu.removeAction(action)
            action.deleteLater()

        if tuple(previous) != tuple(ordered):
            before = None
            for action in reversed(ordered.values()):
                menu.insertAction(before, action)
                before = action
        previous.clear()
        previous.update(ordered)
