"""Nonactivating windows for the sprites, task panel, and short messages."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pokemac.engine import MAX_LEVEL, xp_for_level


SPRITE_SIZE = 96
PILL_STYLE = """
    color: #eaf7ee;
    background: #172520;
    border: 1px solid #476757;
    border-radius: 9px;
    padding: 4px 8px;
    font-weight: 600;
"""
PANEL_STYLE = """
    QFrame#card { background: #172520; border: 1px solid #476757; border-radius: 15px; }
    QLabel { color: #eaf7ee; background: transparent; }
    QLabel#name { font-size: 18px; font-weight: 700; }
    QLabel#muted { color: #b3c8bb; }
    QLabel#section { color: #eaf7ee; font-weight: 700; }
    QProgressBar { background: #2d4338; border: none; border-radius: 4px; }
    QProgressBar::chunk { background: #a4e8bb; border-radius: 4px; }
    QPushButton {
        color: #eaf7ee; background: #2c483a; border: 1px solid #4c6a58;
        border-radius: 7px; padding: 4px 9px;
    }
    QPushButton:hover { background: #3b5d4b; }
    QPushButton:disabled { color: #a2b3a8; }
    QScrollArea { background: transparent; border: none; }
"""


def display_name(species):
    return species.replace("-", " ").title()


def sprite_pixmap(image):
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        return QPixmap()
    image = image.crop(bounds)
    width, height = image.size
    qt_image = QImage(
        image.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888,
    ).copy()
    return QPixmap.fromImage(qt_image).scaled(
        SPRITE_SIZE, SPRITE_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )


class OverlayWindow(QWidget):
    def __init__(self):
        flags = (
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class SpriteWindow(OverlayWindow):
    clicked = Signal()
    drag_started = Signal()
    drag_moved = Signal(int, int)
    drag_released = Signal()

    def __init__(self, *, wild=False):
        super().__init__()
        self._wild = wild
        self._press = None
        self._origin = None
        self._dragging = False
        self._direction = 1
        self._pixmap = QPixmap()
        self._mirrored = QPixmap()
        self._name = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.art = QLabel(alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self.art.setTextFormat(Qt.TextFormat.PlainText)
        self.art.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.art.setFixedHeight(SPRITE_SIZE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        if wild:
            self.caption = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            self.caption.setTextFormat(Qt.TextFormat.PlainText)
            self.caption.setWordWrap(True)
            self.caption.setStyleSheet(PILL_STYLE)
            self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(self.caption)
            self.setFixedSize(132, SPRITE_SIZE + 42)
        else:
            self.setFixedSize(SPRITE_SIZE, SPRITE_SIZE)
        layout.addWidget(self.art)

    @property
    def pressed(self):
        return self._press is not None

    def set_species(self, species, pixmap):
        self._name = display_name(species)
        self._pixmap = pixmap
        self._mirrored = pixmap.transformed(QTransform().scale(-1, 1))
        if self._wild:
            self.caption.setText(f"Catch {self._name}")
        else:
            self.setToolTip(f"{self._name} · click for tasks · drag to move")
        self._paint_art()

    def face(self, direction):
        if self._direction != direction:
            self._direction = direction
            self._paint_art()

    def _paint_art(self):
        if self._pixmap.isNull():
            self.art.setText("" if self._wild else self._name)
            self.art.setStyleSheet("" if self._wild else PILL_STYLE)
        else:
            self.art.setStyleSheet("")
            self.art.setPixmap(self._mirrored if self._direction > 0 else self._pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.pressed and not self._wild:
            delta = event.globalPosition().toPoint() - self._press
            if not self._dragging and delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.drag_started.emit()
            if self._dragging:
                position = self._origin + delta
                self.drag_moved.emit(position.x(), position.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.pressed:
            self.mouseMoveEvent(event)
            self._press = self._origin = None
            if self._dragging:
                self._dragging = False
                self.drag_released.emit()
            elif self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()


def _label(text="", style="muted"):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(style)
    return label


def _button(text, callback):
    button = QPushButton(text)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.clicked.connect(callback)
    return button


class CompanionPanel(OverlayWindow):
    task_done = Signal(int)
    quit_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(316)
        self.setStyleSheet(PANEL_STYLE)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="card")
        container.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.name = _label(style="name")
        close = _button("×", self.hide)
        close.setToolTip("Close panel")
        header.addWidget(self.name, 1)
        header.addWidget(close)
        layout.addLayout(header)

        self.progress_text = _label()
        layout.addWidget(self.progress_text)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.progress)
        self.summary = _label()
        layout.addWidget(self.summary)
        layout.addWidget(_label("Open tasks", "section"))

        self._tasks = None
        self.items = QScrollArea(widgetResizable=True)
        self.items.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        task_list = QWidget()
        task_list.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(task_list)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.items.setWidget(task_list)
        layout.addWidget(self.items)
        layout.addWidget(
            _button("Quit overlay", lambda: self.quit_requested.emit()),
            0, Qt.AlignmentFlag.AlignRight,
        )

    def set_busy(self, busy):
        self.items.setEnabled(not busy)

    def update_state(self, status, tasks):
        pokemon = status["active_pokemon"]
        if pokemon is None:
            self.name.setText("No active Pokémon")
            self.progress_text.clear()
            self.progress.hide()
        else:
            self.name.setText(pokemon["nickname"] or display_name(pokemon["species"]))
            level = pokemon["level"]
            if level == MAX_LEVEL:
                self.progress_text.setText(f"Level {level} · {pokemon['xp']} total XP")
                self.progress.setRange(0, 1)
                self.progress.setValue(1)
            else:
                start = xp_for_level(level)
                goal = xp_for_level(level + 1) - start
                earned = pokemon["xp"] - start
                self.progress_text.setText(f"Level {level} · {earned} / {goal} XP")
                self.progress.setRange(0, goal)
                self.progress.setValue(earned)
            self.progress.show()

        inventory = status["inventory"]
        self.summary.setText(f"{status['streak']} day streak · {sum(inventory.values())} balls")
        self.summary.setToolTip(" · ".join(f"{name}: {count}" for name, count in inventory.items()))
        self._update_tasks(tasks)
        self.adjustSize()

    def _update_tasks(self, tasks):
        if self._tasks == tasks:
            return
        self._tasks = tasks
        while self.rows.count():
            self.rows.takeAt(0).widget().deleteLater()
        for task in tasks:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 5, 0)
            title = _label(task["title"], "task")
            title.setWordWrap(True)
            layout.addWidget(title, 1)
            done = _button("Done", lambda _, task_id=task["id"]: self.task_done.emit(task_id))
            layout.addWidget(done)
            self.rows.addWidget(row)
        if not tasks:
            self.rows.addWidget(_label("No open tasks"))
        self.items.setFixedHeight(min(176, self.rows.sizeHint().height() + 4))


class Message(OverlayWindow):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput)
        self.label = _label()
        self.label.setMaximumWidth(306)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(PILL_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)

    def set_message(self, text):
        self.label.setText(text)
        self.adjustSize()
