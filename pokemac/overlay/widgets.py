"""Nonactivating windows for the sprites, task panel, and short messages."""

from PySide6.QtCore import QEvent, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QBitmap, QColor, QImage, QPainter, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsColorizeEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pokemac.engine import MAX_LEVEL, xp_for_level
from pokemac.overlay.presentation import collection_rows, display_name


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
    QRadioButton { color: #eaf7ee; spacing: 6px; }
    QScrollArea { background: transparent; border: none; }
    QTableWidget { background: #20332a; color: #eaf7ee; border: none; }
    QHeaderView::section { background: #2c483a; color: #eaf7ee; padding: 5px; border: none; }
"""


def sprite_pixmap(image, *, ratio=1.0, smooth=False):
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        return QPixmap()
    image = image.crop(bounds)
    width, height = image.size
    qt_image = QImage(
        image.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888,
    ).copy()
    pixels = max(1, round(SPRITE_SIZE * ratio))
    transform = (
        Qt.TransformationMode.SmoothTransformation if smooth
        else Qt.TransformationMode.FastTransformation
    )
    pixmap = QPixmap.fromImage(qt_image).scaled(
        pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio, transform,
    )
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class OverlayWindow(QWidget):
    def __init__(self):
        flags = (
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class SpriteWindow(OverlayWindow):
    clicked = Signal()
    drag_started = Signal()
    drag_moved = Signal(int, int)
    drag_released = Signal()
    scale_changed = Signal()

    def __init__(self, *, wild=False):
        super().__init__()
        self._wild = wild
        self._press = None
        self._origin = None
        self._dragging = False
        self._direction = 1
        self._napping = False
        self._pixmap = QPixmap()
        self._mirrored = QPixmap()
        self._hit_region = QRegion()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.art = QLabel(alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self.art.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.art.setFixedHeight(SPRITE_SIZE)

        self._glow = QGraphicsColorizeEffect(self.art)
        self._glow.setColor(QColor("#f2ffdc"))
        self._glow.setEnabled(False)
        self.art.setGraphicsEffect(self._glow)
        self._flash = QPropertyAnimation(self._glow, b"strength", self)
        self._flash.setDuration(640)
        self._flash.setStartValue(0.0)
        self._flash.setKeyValueAt(0.22, 0.95)
        self._flash.setEndValue(0.0)
        self._flash.finished.connect(lambda: self._glow.setEnabled(False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setFixedSize(SPRITE_SIZE, SPRITE_SIZE)
        layout.addWidget(self.art)

    @property
    def pressed(self):
        return self._press is not None

    def set_species(self, species, pixmap):
        name = display_name(species)
        self.setToolTip(f"Catch {name}" if self._wild else f"{name} · click for tasks · drag to move")
        self._pixmap = self._name_pixmap(name) if pixmap.isNull() else pixmap
        self._mirrored = self._pixmap
        if not pixmap.isNull():
            self._mirrored = pixmap.transformed(QTransform().scale(-1, 1))
            self._mirrored.setDevicePixelRatio(pixmap.devicePixelRatio())
        self._paint_art()

    def _name_pixmap(self, name):
        pixmap = QPixmap(SPRITE_SIZE, SPRITE_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        with QPainter(pixmap) as painter:
            painter.setPen(QColor("#65b78d"))
            flags = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap)
            painter.drawText(pixmap.rect(), flags, name)
        return pixmap

    def face(self, direction):
        if self._direction != direction:
            self._direction = direction
            self._paint_art()

    def _paint_art(self):
        sprite = self._mirrored if self._direction > 0 else self._pixmap
        if sprite.isNull():
            return
        self.art.setPixmap(sprite)
        size = sprite.deviceIndependentSize().toSize()
        image = sprite.toImage().scaled(size)
        if image.hasAlphaChannel():
            region = QRegion(QBitmap.fromImage(image.createAlphaMask()))
        else:
            region = QRegion(0, 0, size.width(), size.height())
        self._hit_region = region.translated((SPRITE_SIZE - size.width()) // 2, SPRITE_SIZE - size.height())
        self.setMask(self._hit_region)

    def set_napping(self, napping):
        if self._napping != napping:
            self._napping = napping
            self.setWindowOpacity(0.78 if napping else 1.0)

    def flash(self):
        self._flash.stop()
        self._glow.setEnabled(True)
        self._flash.start()

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.scale_changed.emit()
        return handled

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._hit_region.contains(event.position().toPoint()):
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            event.accept()
        else:
            event.ignore()

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
            distance = (event.globalPosition().toPoint() - self._press).manhattanLength()
            self.mouseMoveEvent(event)
            self._press = self._origin = None
            if self._dragging:
                self._dragging = False
                self.drag_released.emit()
            elif distance < QApplication.startDragDistance():
                # The press hit visible pixels. Artwork may have changed since then.
                self.clicked.emit()
            event.accept()

    def hideEvent(self, event):
        self._press = self._origin = None
        if self._dragging:
            self._dragging = False
            self.drag_released.emit()
        super().hideEvent(event)


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
    task_done = Signal(object, object)
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

        self._choices = ()
        self._chosen_recipient = None
        self._recipient_id = None
        self._buttons = {}
        self.recipients = QWidget()
        recipient_layout = QVBoxLayout(self.recipients)
        recipient_layout.setContentsMargins(0, 0, 0, 0)
        recipient_layout.setSpacing(4)
        recipient_layout.addWidget(_label("Full XP recipient", "section"))
        self._recipient_layout = QVBoxLayout()
        self._recipient_layout.setSpacing(3)
        recipient_layout.addLayout(self._recipient_layout)
        self._recipient_group = QButtonGroup(self)
        layout.addWidget(self.recipients)
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
        self.recipients.setEnabled(not busy)

    def reset_recipient(self):
        self._chosen_recipient = None
        self._select_recipient()

    def _select_recipient(self):
        lead_id = self._choices[0][0] if self._choices else None
        selected = self._chosen_recipient if self._chosen_recipient in self._buttons else lead_id
        self._recipient_id = selected
        if selected is not None:
            self._buttons[selected].setChecked(True)

    def _pick_recipient(self, pokemon_id):
        self._chosen_recipient = self._recipient_id = pokemon_id

    def _update_recipients(self, party):
        choices = tuple(
            (pokemon["id"], pokemon["nickname"] or display_name(pokemon["species"]),
             pokemon["is_active"])
            for pokemon in party
        )
        if choices == self._choices:
            return
        self._choices = choices
        for button in self._buttons.values():
            self._recipient_group.removeButton(button)
            self._recipient_layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()
        for pokemon_id, name, lead in choices:
            role = " · Lead" if lead else ""
            button = QRadioButton(f"{name} · #{pokemon_id}{role}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._recipient_group.addButton(button)
            button.clicked.connect(lambda _, selected=pokemon_id: self._pick_recipient(selected))
            self._recipient_layout.addWidget(button)
            self._buttons[pokemon_id] = button
        if self._chosen_recipient not in self._buttons:
            self._chosen_recipient = None
        self._select_recipient()
        self.recipients.setVisible(len(choices) > 1)

    def update_state(self, status, tasks, pokemon):
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

        inventory = status["inventory"]
        self.summary.setText(f"{status['streak']} day streak · {sum(inventory.values())} balls")
        self.summary.setToolTip(" · ".join(f"{name}: {count}" for name, count in inventory.items()))
        self._update_recipients(status["party"])
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
            done = _button(
                "Done", lambda _, task_id=task["id"]: self.task_done.emit(task_id, self._recipient_id),
            )
            layout.addWidget(done)
            self.rows.addWidget(row)
        if not tasks:
            self.rows.addWidget(_label("No open tasks"))
        self.items.setFixedHeight(min(176, self.rows.sizeHint().height() + 4))


class CollectionWindow(OverlayWindow):
    def __init__(self):
        super().__init__()
        self.resize(525, 320)
        self.setStyleSheet(PANEL_STYLE)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="card")
        container.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 12, 15, 12)

        header = QHBoxLayout()
        header.addWidget(_label("Collection", "name"), 1)
        close = _button("×", self.hide)
        close.setToolTip("Close collection")
        header.addWidget(close)
        layout.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("ID", "Species", "Level", "Caught", "Party"))
        self.table.verticalHeader().hide()
        columns = self.table.horizontalHeader()
        columns.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        columns.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.table)
        self._rows = None

    def update_collection(self, collection, party):
        rows = collection_rows(collection, party)
        if rows == self._rows:
            return
        self._rows = rows
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.table.setItem(index, column, QTableWidgetItem(value))


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
