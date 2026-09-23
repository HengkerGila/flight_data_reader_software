"""Theme switching (Settings → Theme).

A theme is either *System default* (the palette and style the platform gave
the application at start-up) or a named palette applied on top of the
Fusion style, which draws every widget from the palette, so a theme is
complete and looks the same on every platform.  Where Qt offers it (6.8+)
the light / dark colour-scheme hint is set as well so tooltips and native
dialogs follow.  The choice is remembered in QSettings and applied at
start-up before the main window is built.

Widget code never hard-codes background colours: tints use alpha over the
palette base, and the few fixed foreground colours (status red / orange /
green) were chosen to read on light and dark backgrounds alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

SETTINGS_ORGANIZATION = "arinc717_reader"
SETTINGS_APPLICATION = "ARINC717Reader"
SETTINGS_KEY_THEME = "ui/theme"


# ---------------------------------------------------------------------------
# Palette construction
# ---------------------------------------------------------------------------


def _build_palette(
    *,
    window: str,
    base: str,
    alternate_base: str,
    text: str,
    disabled_text: str,
    highlight: str,
    highlighted_text: str,
    link: str,
    link_visited: str,
    tooltip_base: str | None = None,
    tooltip_text: str | None = None,
    button: str | None = None,
    button_text: str | None = None,
    placeholder: str | None = None,
    bright_text: str = "#ff5a5a",
    disabled_highlight: str | None = None,
) -> QPalette:
    """A complete palette from a handful of colours.

    ``QPalette(QColor)`` derives the 3D roles (Light, Midlight, Dark, Mid,
    Shadow) from the button colour, so every Fusion frame and bevel matches.
    """
    button_color = QColor(button or window)
    palette = QPalette(button_color)
    roles = QPalette.ColorRole
    palette.setColor(roles.Window, QColor(window))
    palette.setColor(roles.WindowText, QColor(text))
    palette.setColor(roles.Base, QColor(base))
    palette.setColor(roles.AlternateBase, QColor(alternate_base))
    palette.setColor(roles.ToolTipBase, QColor(tooltip_base or alternate_base))
    palette.setColor(roles.ToolTipText, QColor(tooltip_text or text))
    palette.setColor(roles.Text, QColor(text))
    palette.setColor(roles.PlaceholderText, QColor(placeholder or disabled_text))
    palette.setColor(roles.Button, button_color)
    palette.setColor(roles.ButtonText, QColor(button_text or text))
    palette.setColor(roles.BrightText, QColor(bright_text))
    palette.setColor(roles.Link, QColor(link))
    palette.setColor(roles.LinkVisited, QColor(link_visited))
    palette.setColor(roles.Highlight, QColor(highlight))
    palette.setColor(roles.HighlightedText, QColor(highlighted_text))
    group = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(group, role, QColor(disabled_text))
    palette.setColor(group, roles.Highlight, QColor(disabled_highlight or alternate_base))
    palette.setColor(group, roles.HighlightedText, QColor(disabled_text))
    palette.setColor(group, roles.Base, QColor(alternate_base))
    return palette


def light_palette() -> QPalette:
    """Fusion's standard light palette, independent of the platform theme."""
    style = QStyleFactory.create("Fusion")
    return style.standardPalette() if style is not None else QPalette()


def dark_palette() -> QPalette:
    """A dark palette in the Fusion tradition: grey chrome, near-black bases."""
    return _build_palette(
        window="#353535",
        base="#232323",
        alternate_base="#2d2d2d",
        text="#e1e1e1",
        disabled_text="#7f7f7f",
        highlight="#2a82da",
        highlighted_text="#ffffff",
        link="#60a5ff",
        link_visited="#b482ff",
        tooltip_base="#3c3c3c",
        placeholder="#8c8c8c",
        disabled_highlight="#505050",
    )


def solarized_light_palette() -> QPalette:
    return _build_palette(
        window="#eee8d5",
        base="#fdf6e3",
        alternate_base="#f3ecd9",
        text="#586e75",
        disabled_text="#a4a89b",
        highlight="#268bd2",
        highlighted_text="#fdf6e3",
        link="#268bd2",
        link_visited="#6c71c4",
        tooltip_base="#fdf6e3",
        button="#e8e1cc",
        placeholder="#93a1a1",
        bright_text="#dc322f",
    )


def sepia_palette() -> QPalette:
    return _build_palette(
        window="#efe4cf",
        base="#faf3e4",
        alternate_base="#f2e9d6",
        text="#4a3a28",
        disabled_text="#a08e74",
        highlight="#a9743a",
        highlighted_text="#fff8ec",
        link="#8a5a1e",
        link_visited="#6f4a7a",
        tooltip_base="#faf3e4",
        button="#e6d9c0",
        placeholder="#9c8a70",
        bright_text="#b3261e",
    )


def solarized_dark_palette() -> QPalette:
    return _build_palette(
        window="#073642",
        base="#002b36",
        alternate_base="#0a3c49",
        text="#93a1a1",
        disabled_text="#586e75",
        highlight="#268bd2",
        highlighted_text="#fdf6e3",
        link="#2aa198",
        link_visited="#6c71c4",
        tooltip_base="#0a3c49",
        tooltip_text="#eee8d5",
        button="#0d4452",
        button_text="#eee8d5",
        placeholder="#657b83",
        bright_text="#dc322f",
    )


def nord_palette() -> QPalette:
    return _build_palette(
        window="#2e3440",
        base="#3b4252",
        alternate_base="#434c5e",
        text="#eceff4",
        disabled_text="#7b8394",
        highlight="#88c0d0",
        highlighted_text="#2e3440",
        link="#88c0d0",
        link_visited="#b48ead",
        tooltip_base="#434c5e",
        button="#3b4252",
        placeholder="#8f97a6",
        bright_text="#bf616a",
    )


def midnight_palette() -> QPalette:
    return _build_palette(
        window="#1b2230",
        base="#121826",
        alternate_base="#1f2836",
        text="#d6dde8",
        disabled_text="#6e7a8c",
        highlight="#3d7bd9",
        highlighted_text="#ffffff",
        link="#7fb2ff",
        link_visited="#c39bff",
        tooltip_base="#26304a",
        button="#242d3d",
        placeholder="#7f8a9c",
        bright_text="#ff6b6b",
    )


def cockpit_palette() -> QPalette:
    """Black panel with green readouts and amber highlights (avionics style)."""
    return _build_palette(
        window="#0c100c",
        base="#050805",
        alternate_base="#101810",
        text="#8fe58f",
        disabled_text="#3f6b3f",
        highlight="#d99a1e",
        highlighted_text="#050805",
        link="#e6b34a",
        link_visited="#c9a0dc",
        tooltip_base="#182418",
        tooltip_text="#c6f0c6",
        button="#141c14",
        button_text="#a9f0a9",
        placeholder="#4f8a4f",
        bright_text="#ff5050",
    )


def high_contrast_palette() -> QPalette:
    return _build_palette(
        window="#000000",
        base="#000000",
        alternate_base="#1a1a1a",
        text="#ffffff",
        disabled_text="#a0a0a0",
        highlight="#ffff00",
        highlighted_text="#000000",
        link="#66ccff",
        link_visited="#ff99ff",
        tooltip_base="#000000",
        button="#202020",
        placeholder="#c0c0c0",
        bright_text="#ff4040",
        disabled_highlight="#404040",
    )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    id: str
    label: str
    dark: bool
    palette: Callable[[], QPalette] | None  # None = the platform's own palette
    description: str = ""


THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"

THEME_CATALOGUE: tuple[Theme, ...] = (
    Theme(THEME_SYSTEM, "System default", False, None, "The colours and widget style of the operating system."),
    Theme(THEME_LIGHT, "Light", False, light_palette, "Neutral light grey (Fusion)."),
    Theme("solarized-light", "Solarized Light", False, solarized_light_palette, "Warm cream with blue accents."),
    Theme("sepia", "Sepia", False, sepia_palette, "Paper-like warm tones, easy on the eyes in daylight."),
    Theme(THEME_DARK, "Dark", True, dark_palette, "Neutral dark grey."),
    Theme("solarized-dark", "Solarized Dark", True, solarized_dark_palette, "Deep teal with muted text."),
    Theme("nord", "Nord", True, nord_palette, "Blue-grey polar night with frost accents."),
    Theme("midnight", "Midnight Blue", True, midnight_palette, "Navy panels with bright blue selection."),
    Theme("cockpit", "Cockpit", True, cockpit_palette, "Black panel, green readouts, amber highlights."),
    Theme("high-contrast", "High Contrast", True, high_contrast_palette, "Black and white with yellow selection."),
)
THEME_BY_ID = {theme.id: theme for theme in THEME_CATALOGUE}
THEMES = tuple(theme.id for theme in THEME_CATALOGUE)
THEME_LABELS = {theme.id: theme.label for theme in THEME_CATALOGUE}
# Menu groups, in order: the system entry, the light themes, the dark themes.
THEME_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("System", (THEME_SYSTEM,)),
    ("Light themes", tuple(t.id for t in THEME_CATALOGUE if t.palette is not None and not t.dark)),
    ("Dark themes", tuple(t.id for t in THEME_CATALOGUE if t.palette is not None and t.dark)),
)


def theme_palette(theme_id: str) -> QPalette | None:
    """The palette of a named theme; None for the system theme."""
    theme = THEME_BY_ID[theme_id]
    return theme.palette() if theme.palette is not None else None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def open_settings() -> QSettings:
    """The application's settings store (registry on Windows, .conf elsewhere)."""
    return QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)


def load_theme_preference(settings: QSettings | None = None) -> str:
    settings = settings or open_settings()
    value = settings.value(SETTINGS_KEY_THEME, THEME_SYSTEM)
    return value if value in THEME_BY_ID else THEME_SYSTEM


def save_theme_preference(theme: str, settings: QSettings | None = None) -> None:
    if theme not in THEME_BY_ID:
        raise ValueError(f"unknown theme {theme!r}")
    settings = settings or open_settings()
    settings.setValue(SETTINGS_KEY_THEME, theme)
    settings.sync()


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ThemeManager:
    """Applies a theme to the running application and remembers the start-up look."""

    def __init__(self, app: QApplication):
        self._app = app
        self._system_palette = QPalette(app.palette())
        self._system_style_name = app.style().objectName()
        self.current = THEME_SYSTEM
        self._listeners: list[Callable[[str], None]] = []

    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._listeners.append(callback)

    def apply(self, theme_id: str) -> None:
        theme = THEME_BY_ID.get(theme_id)
        if theme is None:
            raise ValueError(f"unknown theme {theme_id!r}")
        app = self._app
        hints = QGuiApplication.styleHints()
        if theme.palette is None:
            self._set_color_scheme(hints, Qt.ColorScheme.Unknown)
            style = QStyleFactory.create(self._system_style_name)
            if style is not None:
                app.setStyle(style)
            app.setPalette(self._system_palette)
        else:
            app.setStyle("Fusion")
            self._set_color_scheme(
                hints, Qt.ColorScheme.Dark if theme.dark else Qt.ColorScheme.Light
            )
            app.setPalette(theme.palette())
        self.current = theme.id
        for callback in list(self._listeners):
            callback(theme.id)

    @staticmethod
    def _set_color_scheme(hints, scheme) -> None:
        setter = getattr(hints, "setColorScheme", None)  # Qt 6.8+
        if setter is None:
            return
        try:
            setter(scheme)
        except Exception:  # pragma: no cover - platform theme without support
            pass
