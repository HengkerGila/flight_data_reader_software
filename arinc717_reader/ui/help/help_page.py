"""Help page: a section list, a search box and a rich-text guide.

The guide text lives in ``help_content.py`` (plain Python, so it ships
inside the package and needs no data files in a frozen build).  The page
only renders it; it never touches the stores.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .help_content import HELP_SECTIONS, help_images_dir, render_html

STYLE_SHEET = """
h1 { font-size: 20pt; margin-bottom: 4px; }
h2 { font-size: 15pt; margin-top: 22px; margin-bottom: 4px; }
h3 { font-size: 12pt; margin-top: 14px; margin-bottom: 2px; }
p, li { font-size: 10.5pt; }
code { font-family: monospace; }
table { border-collapse: collapse; }
th { text-align: left; padding: 3px 8px; }
td { padding: 3px 8px; vertical-align: top; }
"""


class HelpPage(QWidget):
    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Find text in the guide (Enter = next match)")
        self._search.setClearButtonEnabled(True)
        self._search.returnPressed.connect(self.find_next)
        top.addWidget(self._search, 1)
        find = QPushButton("Find next")
        find.clicked.connect(self.find_next)
        top.addWidget(find)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._sections = QListWidget()
        for section in HELP_SECTIONS:
            item = QListWidgetItem(("    " if section.level > 1 else "") + section.title)
            item.setData(Qt.ItemDataRole.UserRole, section.anchor)
            self._sections.addItem(item)
        self._sections.currentItemChanged.connect(self._section_selected)
        splitter.addWidget(self._sections)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._anchor_clicked)
        self._images_dir = help_images_dir()
        self._browser.setSearchPaths([str(self._images_dir)])  # <img src="folder/name.png">
        document = QTextDocument(self._browser)
        self._browser.setDocument(document)
        self._render()
        splitter.addWidget(self._browser)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter)

    # -- rendering -------------------------------------------------------

    def _render(self) -> None:
        """(Re)build the document with link colours taken from the current palette.

        Qt resolves anchor colours when the HTML is parsed, not when it is
        painted, so a theme change would otherwise leave the links in the
        old theme's colour (dark blue on a dark background).
        """
        # The page's own palette is current when its PaletteChange arrives; the
        # child browser's palette is only resolved afterwards.
        palette = self.palette()
        link = palette.color(QPalette.ColorRole.Link).name()
        visited = palette.color(QPalette.ColorRole.LinkVisited).name()
        if getattr(self, "_rendered_links", None) == (link, visited):
            return  # same colours: nothing to redo (PaletteChange arrives more than once)
        self._rendered_links = (link, visited)
        document = self._browser.document()
        document.setDefaultStyleSheet(
            STYLE_SHEET + f"\na {{ color: {link}; }}\na:visited {{ color: {visited}; }}\n"
        )
        scrollbar = self._browser.verticalScrollBar()
        position = scrollbar.value()
        self._browser.setHtml(render_html(self._images_dir))
        scrollbar.setValue(position)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._render()

    # -- navigation ------------------------------------------------------

    def show_section(self, anchor: str) -> None:
        for row in range(self._sections.count()):
            item = self._sections.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == anchor:
                self._sections.setCurrentItem(item)
                break
        self._browser.scrollToAnchor(anchor)

    def _section_selected(self, current, _previous) -> None:
        if current is not None:
            self._browser.scrollToAnchor(current.data(Qt.ItemDataRole.UserRole))

    def _anchor_clicked(self, url) -> None:
        fragment = url.fragment() or url.toString().lstrip("#")
        if fragment:
            self.show_section(fragment)

    def find_next(self) -> None:
        text = self._search.text().strip()
        if not text:
            return
        if not self._browser.find(text):
            # Wrap around.
            cursor = self._browser.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self._browser.setTextCursor(cursor)
            self._browser.find(text)

    @property
    def section_anchors(self) -> list[str]:
        return [section.anchor for section in HELP_SECTIONS]

    def plain_text(self) -> str:
        return self._browser.toPlainText()

    @property
    def images_dir(self):
        return self._images_dir
