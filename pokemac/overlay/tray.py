"""Menu bar controls for the companion."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
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

    def __init__(self, parent):
        super().__init__(_tray_icon(), parent)
        self.setToolTip("Pokemac")
        self._overlay_visible = True
        self._choices = None
        self._menu = QMenu()
        self._toggle = self._menu.addAction("Hide overlay")
        self._toggle.triggered.connect(lambda: self.visibility_requested.emit(not self._overlay_visible))
        self._party = self._menu.addMenu("Party")
        self._add = self._party.addMenu("Add to party")
        self._remove = self._party.addMenu("Remove from party")
        self._lead = self._party.addMenu("Make lead")
        self._party.setEnabled(False)
        self._menu.addAction("Collection", lambda: self.collection_requested.emit())
        self._menu.addSeparator()
        self._menu.addAction("Quit", lambda: self.quit_requested.emit())
        self.setContextMenu(self._menu)

    def set_overlay_visible(self, visible):
        self._overlay_visible = visible
        self._toggle.setText("Hide overlay" if visible else "Show overlay")

    def set_busy(self, busy):
        self._party.setEnabled(bool(self._choices) and not busy)

    def update_party(self, collection, party):
        member_ids = {pokemon["id"] for pokemon in party}
        add = [pokemon for pokemon in collection if pokemon["id"] not in member_ids]
        choices = tuple(
            (pokemon["id"], pokemon["nickname"] or display_name(pokemon["species"]),
             pokemon["level"], pokemon["id"] in member_ids, pokemon["is_active"])
            for pokemon in collection
        )
        if choices == self._choices:
            return
        self._choices = choices
        self._refresh_menu(self._add, add, self.add_requested)
        self._add.setEnabled(bool(add) and len(party) < PARTY_LIMIT)
        self._refresh_menu(self._remove, party, self.remove_requested)
        others = [pokemon for pokemon in party if not pokemon["is_active"]]
        self._refresh_menu(self._lead, others, self.lead_requested)
        self.set_busy(False)

    def _refresh_menu(self, menu, entries, requested):
        menu.clear()
        menu.setEnabled(bool(entries))
        for pokemon in entries:
            pokemon_id = pokemon["id"]
            name = pokemon["nickname"] or display_name(pokemon["species"])
            suffix = " · Lead" if pokemon["is_active"] else ""
            action = menu.addAction(f"{name} · Lv. {pokemon['level']} · #{pokemon_id}{suffix}")
            action.triggered.connect(
                lambda _, selected=pokemon_id: requested.emit(selected),
            )
