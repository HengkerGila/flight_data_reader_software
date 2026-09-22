"""Small shared UI helpers."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from ..dataframe.validator import SEVERITY_ERROR, ValidationIssue

COLOR_ERROR = QColor(200, 60, 60)
COLOR_WARNING = QColor(190, 130, 30)
COLOR_OK = QColor(60, 150, 90)


def monospace_font():
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


def make_issues_table(parent=None) -> QTableWidget:
    table = QTableWidget(0, 4, parent)
    table.setHorizontalHeaderLabels(["Severity", "Rule", "Parameter", "Message"])
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    return table


def fill_issues_table(table: QTableWidget, issues: list[ValidationIssue]) -> None:
    table.setRowCount(len(issues))
    for row, issue in enumerate(issues):
        severity = QTableWidgetItem(issue.severity.upper())
        severity.setForeground(
            COLOR_ERROR if issue.severity == SEVERITY_ERROR else COLOR_WARNING
        )
        table.setItem(row, 0, severity)
        table.setItem(row, 1, QTableWidgetItem(issue.rule_name))
        table.setItem(row, 2, QTableWidgetItem(issue.parameter_id or ""))
        table.setItem(row, 3, QTableWidgetItem(issue.message))
