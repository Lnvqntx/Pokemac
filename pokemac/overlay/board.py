"""Desktop quest board for daily offers, open tasks, rewards and the collection."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from pokemac.engine import DIFFICULTIES, POKEMON_TYPES, TASK_TYPES
from pokemac.overlay.presentation import display_name
from pokemac.overlay.widgets import CollectionTable, PANEL_STYLE, TaskTitle, _button, _label, task_row


BOARD_STYLE = PANEL_STYLE + """
    QWidget#board, QTabWidget::pane, QScrollArea > QWidget > QWidget { background: #172520; }
    QTabBar::tab {
        background: #263c32; color: #eaf7ee; padding: 7px 16px;
        border: 1px solid #476757;
    }
    QTabBar::tab:selected { background: #3b5d4b; }
    QGroupBox { color: #eaf7ee; font-weight: bold; }
    QLineEdit {
        color: #eaf7ee; background: #20332a; border: 1px solid #476757;
        border-radius: 6px; padding: 5px;
    }
"""


class QuestList(QScrollArea):
    def __init__(self):
        super().__init__(widgetResizable=True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.rows = QVBoxLayout(content)
        self.rows.setContentsMargins(2, 2, 6, 2)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(content)
        self._entries = None

    def update_rows(self, entries, make_row, empty_text):
        if entries == self._entries:
            return
        self._entries = entries
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        if entries:
            for entry in entries:
                self.rows.addWidget(make_row(entry))
        else:
            self.rows.addWidget(_label(empty_text))


def _completed_row(reward):
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 2, 0, 5)
    layout.setSpacing(2)
    layout.addWidget(TaskTitle(reward["title"]))
    if reward["xp"] is None:
        details = ["Reward details unavailable for this older completion."]
    else:
        details = [f"+{reward['xp']} XP"]
        details.extend(f"+{count} {display_name(ball)}" for ball, count in reward["balls"].items())
        if reward["weekly_bonus"]:
            details.append(f"+{reward['weekly_bonus']} weekly Poké Balls")
        if reward["encounter"]:
            details.append(f"Wild {display_name(reward['encounter'])} encounter")
    label = _label(" · ".join(details))
    label.setWordWrap(True)
    layout.addWidget(label)
    return row


class QuestBoard(QWidget):
    task_done = Signal(object)
    reroll_requested = Signal()
    add_requested = Signal(str, str, str, object)

    def __init__(self):
        super().__init__(objectName="board")
        self.setWindowTitle("Pokemac Quest Board")
        self.resize(625, 525)
        self.setStyleSheet(BOARD_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        today = QWidget()
        today_layout = QVBoxLayout(today)
        today_header = QHBoxLayout()
        today_header.addWidget(_label("Resets at 5 am local time"), 1)
        self.reroll = _button("Reroll", self.reroll_requested.emit)
        today_header.addWidget(self.reroll)
        today_layout.addLayout(today_header)
        self.today = QuestList()
        today_layout.addWidget(self.today)
        self.tabs.addTab(today, "Today")

        quests = QWidget()
        quests_layout = QVBoxLayout(quests)
        self.quests = QuestList()
        quests_layout.addWidget(self.quests, 1)
        quests_layout.addWidget(self._add_form())
        self.tabs.addTab(quests, "Quests")

        self.completed = QuestList()
        self.tabs.addTab(self.completed, "Completed")
        self.collection = CollectionTable()
        self.tabs.addTab(self.collection, "Collection")

        self.feedback = _label()
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self._can_reroll = False
        self.reroll.setEnabled(False)

    def _add_form(self):
        group = QGroupBox("Add quest")
        fields = QFormLayout(group)
        self.title_input = QLineEdit(placeholderText="What will you do?")
        self.kind = QComboBox()
        for kind in TASK_TYPES:
            self.kind.addItem(kind.replace("_", " ").capitalize(), kind)
        self.difficulty = QComboBox()
        for difficulty in DIFFICULTIES:
            self.difficulty.addItem(difficulty.title(), difficulty)
        self.training_type = QComboBox()
        for pokemon_type in POKEMON_TYPES:
            self.training_type.addItem(pokemon_type.title(), pokemon_type)
        fields.addRow("Title", self.title_input)
        fields.addRow("Type", self.kind)
        fields.addRow("Pokémon type", self.training_type)
        fields.addRow("Difficulty", self.difficulty)
        self.add_button = QPushButton("Add quest")
        self.add_button.clicked.connect(self._submit)
        self.title_input.returnPressed.connect(self._submit)
        fields.addRow("", self.add_button)
        self.kind.currentIndexChanged.connect(
            lambda: fields.setRowVisible(self.training_type, self.kind.currentData() == "training")
        )
        fields.setRowVisible(self.training_type, False)
        return group

    def _submit(self):
        if not self.add_button.isEnabled():
            return
        title = self.title_input.text().strip()
        if not title:
            self.show_feedback("Enter a title for the quest.")
            return
        kind = self.kind.currentData()
        pokemon_type = self.training_type.currentData() if kind == "training" else None
        self.add_requested.emit(title, kind, self.difficulty.currentData(), pokemon_type)

    def quest_added(self, _result):
        self.title_input.clear()
        self.show_feedback("Quest added.")

    def show_feedback(self, message):
        self.feedback.setText(message)
        self.feedback.setVisible(bool(message))

    def set_busy(self, busy):
        for widget in (self.today, self.quests, self.add_button, self.title_input,
                       self.kind, self.training_type, self.difficulty):
            widget.setEnabled(not busy)
        self.reroll.setEnabled(self._can_reroll and not busy)

    def update_state(self, board, tasks, collection, party):
        self._can_reroll = board["can_reroll"]
        self.reroll.setEnabled(self._can_reroll)
        self.reroll.setToolTip("Replace unfinished side quests once a day.")
        self.today.update_rows(board["today"], self._task_row, "Daily side quests are available after 5 am.")
        self.quests.update_rows(tasks, self._task_row, "No open quests.")
        self.completed.update_rows(board["completed"], _completed_row, "No completed quests yet.")
        self.collection.update_collection(collection, party)

    def _task_row(self, task):
        return task_row(task, self.task_done.emit)
