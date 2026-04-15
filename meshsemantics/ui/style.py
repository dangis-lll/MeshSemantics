from __future__ import annotations

APP_QSS = """
QMainWindow, QWidget {
    background: #f5f8fc;
    color: #20324a;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}
QMenuBar, QStatusBar {
    background: rgba(248, 251, 255, 0.98);
    color: #355072;
    border-top: 1px solid rgba(114, 151, 208, 0.10);
}
QToolBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(250, 252, 255, 0.98),
        stop:1 rgba(238, 245, 255, 0.98));
    border: none;
    border-bottom: 1px solid rgba(114, 151, 208, 0.18);
    spacing: 8px;
    padding: 8px 10px;
}
QToolButton, QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(66, 133, 244, 0.96),
        stop:1 rgba(86, 160, 255, 0.94));
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.42);
    border-radius: 10px;
    padding: 8px 14px;
    min-height: 16px;
}
QToolButton:hover, QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(46, 123, 245, 1.0),
        stop:1 rgba(104, 178, 255, 0.96));
}
QToolButton:pressed, QPushButton:pressed {
    background: rgba(52, 111, 210, 0.95);
}
QToolButton#undo-button, QToolButton#redo-button {
    padding: 8px;
    min-width: 40px;
    min-height: 16px;
    max-width: 40px;
    max-height: 34px;
}
QDockWidget {
    color: #21334d;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    text-align: left;
    padding: 12px 14px;
    background: rgba(247, 250, 255, 0.98);
    border-bottom: 1px solid rgba(114, 151, 208, 0.16);
}
QLineEdit, QSpinBox, QComboBox, QTableWidget, QTableView, QTreeView, QProgressBar {
    background: rgba(255, 255, 255, 0.98);
    border: 1px solid rgba(132, 162, 210, 0.34);
    border-radius: 10px;
    padding: 6px 8px;
    selection-background-color: rgba(99, 158, 255, 0.22);
    selection-color: #1f3150;
}
QHeaderView::section {
    background: rgba(241, 246, 255, 0.98);
    color: #5676a1;
    border: none;
    border-bottom: 1px solid rgba(132, 162, 210, 0.18);
    padding: 8px;
}
QTreeView::item, QTableWidget::item, QTableView::item {
    padding: 4px;
}
QLabel[role="caption"] {
    color: #6e89ab;
    font-size: 11px;
    text-transform: uppercase;
}
QFrame[panel="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(255, 255, 255, 0.98),
        stop:1 rgba(245, 249, 255, 0.98));
    border: 1px solid rgba(132, 162, 210, 0.20);
    border-radius: 16px;
}
"""
