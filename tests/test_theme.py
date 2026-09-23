"""Theme preference storage and palettes (Settings → Theme)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from arinc717_reader.ui.theme import (  # noqa: E402
    THEME_BY_ID,
    THEME_CATALOGUE,
    THEME_DARK,
    THEME_GROUPS,
    THEME_LIGHT,
    THEME_SYSTEM,
    THEMES,
    ThemeManager,
    dark_palette,
    light_palette,
    load_theme_preference,
    save_theme_preference,
    theme_palette,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settings(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_preference_round_trip_and_validation(tmp_path):
    ini = tmp_path / "settings.ini"
    assert load_theme_preference(_settings(ini)) == THEME_SYSTEM
    save_theme_preference(THEME_DARK, _settings(ini))
    assert load_theme_preference(_settings(ini)) == THEME_DARK
    # An unknown stored value falls back to the system theme instead of failing.
    broken = _settings(ini)
    broken.setValue("ui/theme", "purple")
    broken.sync()
    assert load_theme_preference(_settings(ini)) == THEME_SYSTEM
    with pytest.raises(ValueError):
        save_theme_preference("purple", _settings(ini))


def test_palettes_are_readable(app):
    roles = QPalette.ColorRole
    dark = dark_palette()
    assert dark.color(roles.Window).lightness() < 90
    assert dark.color(roles.Base).lightness() < 60
    assert dark.color(roles.Text).lightness() > 180
    assert dark.color(roles.Link).lightness() > 120  # links stay visible on dark bases
    assert dark.color(QPalette.ColorGroup.Disabled, roles.Text).lightness() < dark.color(roles.Text).lightness()
    light = light_palette()
    assert light.color(roles.Window).lightness() > 200
    assert light.color(roles.Text).lightness() < 80


def test_manager_switches_and_restores(app):
    manager = ThemeManager(app)
    startup = app.palette().color(QPalette.ColorRole.Window)
    manager.apply(THEME_DARK)
    assert manager.current == THEME_DARK
    assert app.palette().color(QPalette.ColorRole.Window).lightness() < 90
    assert app.style().objectName().lower() == "fusion"
    manager.apply(THEME_LIGHT)
    assert app.palette().color(QPalette.ColorRole.Window).lightness() > 200
    seen = []
    manager.subscribe(seen.append)
    manager.apply(THEME_SYSTEM)
    assert seen == [THEME_SYSTEM]
    assert app.palette().color(QPalette.ColorRole.Window) == startup
    with pytest.raises(ValueError):
        manager.apply("no-such-theme")


def test_catalogue_is_consistent(app):
    assert len(THEMES) == len(set(THEMES)) >= 10
    grouped = [tid for _label, ids in THEME_GROUPS for tid in ids]
    assert sorted(grouped) == sorted(THEMES)  # every theme appears in exactly one menu group
    assert THEME_GROUPS[0][1] == (THEME_SYSTEM,)
    assert theme_palette(THEME_SYSTEM) is None
    roles = QPalette.ColorRole
    for theme in THEME_CATALOGUE:
        if theme.palette is None:
            continue
        palette = theme_palette(theme.id)
        base_l = palette.color(roles.Base).lightness()
        text_l = palette.color(roles.Text).lightness()
        window_l = palette.color(roles.Window).lightness()
        # Text must contrast with its background, and the dark flag must match the look.
        assert abs(text_l - base_l) > 100, theme.id
        assert abs(palette.color(roles.WindowText).lightness() - window_l) > 100, theme.id
        assert (window_l < 128) == theme.dark, theme.id
        assert abs(palette.color(roles.HighlightedText).lightness() - palette.color(roles.Highlight).lightness()) > 90, theme.id
        assert abs(palette.color(roles.Link).lightness() - base_l) > 70, theme.id
        disabled = palette.color(QPalette.ColorGroup.Disabled, roles.Text).lightness()
        assert abs(disabled - base_l) < abs(text_l - base_l), theme.id  # disabled is dimmer than text
        assert theme.label and theme.description


def test_manager_applies_every_theme(app):
    manager = ThemeManager(app)
    startup = app.palette().color(QPalette.ColorRole.Window)
    for theme in THEME_CATALOGUE:
        manager.apply(theme.id)
        assert manager.current == theme.id
        if theme.palette is not None:
            expected = theme.palette().color(QPalette.ColorRole.Window)
            assert app.palette().color(QPalette.ColorRole.Window) == expected, theme.id
    manager.apply(THEME_SYSTEM)
    assert app.palette().color(QPalette.ColorRole.Window) == startup
    assert THEME_BY_ID[THEME_DARK].dark and not THEME_BY_ID[THEME_LIGHT].dark
