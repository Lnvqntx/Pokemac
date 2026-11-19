"""Menu bar controls for the companion."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

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
    switch_requested = Signal(int)
    quit_requested = Signal()

    def __init__(self, parent):
        super().__init__(_tray_icon(), parent)
        self.setToolTip("Pokemac")
        self._overlay_visible = True
        self._choices = None
        self._menu = QMenu()
        self._toggle = self._menu.addAction("Hide overlay")
        self._toggle.triggered.connect(lambda: self.visibility_requested.emit(not self._overlay_visible))
        self._switch = self._menu.addMenu("Switch active Pokémon")
        self._switch.setEnabled(False)
        self._menu.addSeparator()
        self._menu.addAction("Quit", lambda: self.quit_requested.emit())
        self.setContextMenu(self._menu)

    def set_overlay_visible(self, visible):
        self._overlay_visible = visible
        self._toggle.setText("Hide overlay" if visible else "Show overlay")

    def set_busy(self, busy):
        self._switch.setEnabled(bool(self._choices) and not busy)

    def update_collection(self, pokemon, active_id):
        choices = tuple(
            (entry["id"], entry["nickname"] or display_name(entry["species"]), entry["level"],
             entry["id"] == active_id)
            for entry in pokemon
        )
        if choices == self._choices:
            return
        self._choices = choices
        self._switch.clear()
        for pokemon_id, name, level, active in choices:
            action = self._switch.addAction(f"{name} · Lv. {level} · #{pokemon_id}")
            action.setCheckable(active)
            action.setChecked(active)
            action.setEnabled(not active)
            action.triggered.connect(
                lambda _, selected=pokemon_id: self.switch_requested.emit(selected),
            )
        self.set_busy(False)
