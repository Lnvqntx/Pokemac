"""Nonactivating windows for the sprites, task panel, and short messages."""

from PySide6.QtCore import QEvent, QPropertyAnimation, QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import (
    QBitmap, QColor, QDesktopServices, QImage, QPainter, QPalette, QPixmap, QRegion,
    QTextLayout, QTextOption, QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsColorizeEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pokemac.engine import BALL_TYPES, MAX_LEVEL, xp_for_level
from pokemac.overlay.presentation import collection_rows, display_name


SPRITE_SIZE = 96
DYNAMAX_SIZE = SPRITE_SIZE * 3 // 2
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
    QComboBox {
        color: #eaf7ee; background: #2c483a; border: 1px solid #4c6a58;
        border-radius: 7px; padding: 4px 9px;
    }
    QScrollArea { background: transparent; border: none; }
    QTableWidget { background: #20332a; color: #eaf7ee; border: none; }
    QHeaderView::section { background: #2c483a; color: #eaf7ee; padding: 5px; border: none; }
"""


def sprite_pixmap(image, *, ratio=1.0, smooth=False, size=SPRITE_SIZE):
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        return QPixmap()
    image = image.crop(bounds)
    width, height = image.size
    qt_image = QImage(
        image.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888,
    ).copy()
    pixels = max(1, round(size * ratio))
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
        self._dynamax = False
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
        self._flash.finished.connect(self._restore_glow)
        self._pulse = QPropertyAnimation(self._glow, b"strength", self)
        self._pulse.setDuration(1600)
        self._pulse.setLoopCount(-1)
        self._pulse.setStartValue(0.15)
        self._pulse.setKeyValueAt(0.5, 0.65)
        self._pulse.setEndValue(0.15)

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
        pixmap = QPixmap(self.width(), self.height())
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
        self._hit_region = region.translated((self.width() - size.width()) // 2, self.height() - size.height())
        self.setMask(self._hit_region)

    def set_napping(self, napping):
        if self._napping != napping:
            self._napping = napping
            self.setWindowOpacity(0.78 if napping else 1.0)

    def flash(self):
        self._pulse.stop()
        self._flash.stop()
        self._glow.setColor(QColor("#f2ffdc"))
        self._glow.setEnabled(True)
        self._flash.start()

    def set_dynamax(self, enabled):
        if self._dynamax != enabled:
            self._dynamax = enabled
            size = DYNAMAX_SIZE if enabled else SPRITE_SIZE
            self.art.setFixedHeight(size)
            self.setFixedSize(size, size)
            self._flash.stop()
            self._restore_glow()

    def _restore_glow(self):
        self._glow.setEnabled(self._dynamax and self.isVisible())
        if self._glow.isEnabled():
            self._glow.setColor(QColor("#ff303e"))
            self._pulse.start()
        else:
            self._pulse.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._restore_glow()

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
        self._flash.stop()
        self._pulse.stop()
        self._glow.setEnabled(False)
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


class TaskTitle(QLabel):
    """Wrap an issue or task title to two lines, with the original in its tooltip."""

    def __init__(self, title):
        super().__init__(textFormat=Qt.TextFormat.PlainText)
        self._title = " ".join(title.split())
        self.setToolTip(title)
        self.setAccessibleName(title)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        metrics = self.fontMetrics()
        self.setFixedHeight(metrics.height() + metrics.lineSpacing())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.contentsRect().width()
        if not self._title or width <= 0:
            return
        layout = QTextLayout(self._title, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        first = layout.createLine()
        first.setLineWidth(width)
        index = first.textLength() * 2  # QTextLine counts UTF-16 code units.
        layout.endLayout()
        encoded = self._title.encode("utf-16-le")
        if index == len(encoded):
            self.setText(self._title)
        else:
            head = encoded[:index].decode("utf-16-le").rstrip()
            tail = encoded[index:].decode("utf-16-le").lstrip()
            elided = self.fontMetrics().elidedText(tail, Qt.TextElideMode.ElideRight, width)
            self.setText(f"{head}\n{elided}")


class CompanionPanel(OverlayWindow):
    task_done = Signal(object)
    lead_requested = Signal()
    form_requested = Signal(str)

    _MAX_HEIGHT = 420
    _WIDTH = 360
    _COMPANION_HEIGHT = 132
    _TASK_GROUPS = (("quest", "Quests"), ("side_quest", "Side quests"), ("training", "Training"))

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#172520"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setFixedWidth(self._WIDTH)
        self.setStyleSheet(PANEL_STYLE + """
            QFrame#card { border-radius: 0; }
            QLabel#difficulty {
                color: #eaf7ee; background: #2d4338; border-radius: 5px;
                padding: 2px 5px; font-size: 11px;
            }
        """)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="card")
        container.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        self.name = _label(style="name")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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

        self.set_lead = _button("Set as lead", self.lead_requested.emit)
        layout.addWidget(self.set_lead)
        self.lead_content = QWidget()
        lead_layout = QVBoxLayout(self.lead_content)
        lead_layout.setContentsMargins(0, 0, 0, 0)
        lead_layout.setSpacing(7)
        layout.addWidget(self.lead_content, 1)

        self.summary = _label()
        lead_layout.addWidget(self.summary)
        self.form_picker = QComboBox()
        self.form_picker.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.form_picker.setAccessibleName("Showcase form")
        self.form_picker.activated.connect(lambda _: self.form_requested.emit(self.form_picker.currentData()))
        self.form_picker.hide()
        lead_layout.addWidget(self.form_picker)

        self.items = QScrollArea(widgetResizable=True)
        self.items.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        task_list = QWidget()
        task_list.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(task_list)
        self.rows.setContentsMargins(0, 0, 4, 0)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.items.setWidget(task_list)
        lead_layout.addWidget(self.items, 1)

        self._is_lead = None
        self._height_limit = self._MAX_HEIGHT
        self._forms = None
        self._task_signature = None

    def fit_screen(self, area):
        width = min(self._WIDTH, max(1, area.width()))
        if width != self.width():
            self.setFixedWidth(width)
        height = area.height()
        limit = min(self._MAX_HEIGHT, max(1, height))
        if limit != self._height_limit:
            self._height_limit = limit
            self._resize_for_role()

    def _resize_for_role(self):
        height = self._MAX_HEIGHT if self._is_lead else self._COMPANION_HEIGHT
        self.setFixedHeight(min(height, self._height_limit))

    def set_busy(self, busy):
        self.items.setEnabled(not busy)
        self.form_picker.setEnabled(not busy)
        self.set_lead.setEnabled(not busy)

    def update_state(self, status, tasks, pokemon, forms):
        name = pokemon["nickname"] or display_name(pokemon["species"])
        if self.name.text() != name:
            self.name.setText(name)
            self.name.setToolTip(name)
        level = pokemon["level"]
        if level == MAX_LEVEL:
            progress_text = f"Level {level} · {pokemon['xp']} total XP"
            goal, earned = 1, 1
        else:
            curve = status["xp_curve"]
            start = xp_for_level(level, curve=curve)
            goal = xp_for_level(level + 1, curve=curve) - start
            earned = pokemon["xp"] - start
            progress_text = f"Level {level} · {earned} / {goal} XP"
        if progress_text != self.progress_text.text():
            self.progress_text.setText(progress_text)
        if self.progress.maximum() != goal:
            self.progress.setRange(0, goal)
        if self.progress.value() != earned:
            self.progress.setValue(earned)

        is_lead = bool(pokemon["is_active"])
        if self._is_lead != is_lead:
            self._is_lead = is_lead
            self.lead_content.setVisible(is_lead)
            self.set_lead.setVisible(not is_lead)
            self._resize_for_role()
        if is_lead:
            inventory = status["inventory"]
            summary = f"{status['streak']} day streak · {sum(inventory.values())} balls"
            if self.summary.text() != summary:
                self.summary.setText(summary)
            balls = " · ".join(f"{name}: {count}" for name, count in inventory.items())
            if self.summary.toolTip() != balls:
                self.summary.setToolTip(balls)
            self._update_forms(forms, pokemon["showcase_form"])
            self._update_tasks(tasks)

    def _update_forms(self, forms, selected):
        if self._forms != forms:
            self._forms = forms
            with QSignalBlocker(self.form_picker):
                self.form_picker.clear()
                for form, label in forms.items():
                    self.form_picker.addItem(label, form)
            self.form_picker.setVisible(len(forms) > 1)
        index = max(0, self.form_picker.findData(selected))
        if self.form_picker.currentIndex() != index:
            self.form_picker.setCurrentIndex(index)

    def _update_tasks(self, tasks):
        signature = tuple(
            (task["id"], task["task_type"], task["title"], task["difficulty"], task.get("training_type"),
             task.get("url")) for task in tasks
        )
        if self._task_signature == signature:
            return
        self._task_signature = signature
        self.items.setUpdatesEnabled(False)
        try:
            while self.rows.count():
                widget = self.rows.takeAt(0).widget()
                widget.hide()
                widget.deleteLater()
            for kind, label in self._TASK_GROUPS:
                group = [task for task in tasks if task["task_type"] == kind]
                self.rows.addWidget(_label(f"{label} ({len(group)})", "section"))
                for task in group:
                    self.rows.addWidget(self._task_row(task))
        finally:
            self.items.setUpdatesEnabled(True)

    def _task_row(self, task):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        title = TaskTitle(task["title"])
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(title)
        tags = QHBoxLayout()
        tags.setContentsMargins(0, 0, 0, 0)
        tags.addWidget(_label(task["difficulty"].title(), "difficulty"))
        if training_type := task.get("training_type"):
            tags.addWidget(_label(training_type.title()))
        tags.addStretch(1)
        text.addLayout(tags)
        layout.addLayout(text, 1)
        if url := task.get("url"):
            button = _button("Open issue", lambda: QDesktopServices.openUrl(QUrl(url)))
            button.setToolTip(url)
        else:
            button = _button("Done", lambda: self.task_done.emit(task["id"]))
        layout.addWidget(button)
        return row


class BallPicker(OverlayWindow):
    ball_selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(290)
        self.setStyleSheet(PANEL_STYLE)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="card")
        container.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 12, 15, 12)
        header = QHBoxLayout()
        self.title = _label(style="name")
        header.addWidget(self.title, 1)
        close = _button("×", self.hide)
        close.setToolTip("Close ball picker")
        header.addWidget(close)
        layout.addLayout(header)
        self.throws = _label()
        layout.addWidget(self.throws)
        choices = QWidget()
        rows = QVBoxLayout(choices)
        rows.setContentsMargins(0, 0, 0, 0)
        self.buttons = {}
        for name in BALL_TYPES:
            button = _button("", lambda _, ball=name: self.ball_selected.emit(ball))
            self.buttons[name] = button
            rows.addWidget(button)
        self.choices = choices
        layout.addWidget(choices)

    def set_busy(self, busy):
        self.choices.setEnabled(not busy)

    def update_state(self, species, inventory, odds, throws):
        self.title.setText(f"Catch {display_name(species)}")
        self.throws.setText(f"Throws left {throws}")
        for name, button in self.buttons.items():
            label = "Poké Ball" if name == "poke-ball" else display_name(name)
            percent = f"{odds[name] * 100:.6f}".rstrip("0").rstrip(".")
            count = inventory[name]
            button.setText(f"{label} · {count} available · {percent}%")
            button.setEnabled(count > 0)
        self.adjustSize()


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
