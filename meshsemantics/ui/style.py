from __future__ import annotations

from meshsemantics.runtime import asset_path


APP_QSS = """
QMainWindow, QWidget {
    background: #f5f8fc;
    color: #20324a;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}
QWidget#viewer_host, QWidget#floating_action_bar {
    background: transparent;
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
    padding: 6px;
    min-width: 56px;
    max-width: 60px;
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
QTabWidget#panel-tabs {
    background: transparent;
}
QTabWidget#panel-tabs::pane {
    background: rgba(247, 250, 255, 0.98);
    border: 1px solid rgba(132, 162, 210, 0.20);
    border-top: none;
    border-bottom-left-radius: 14px;
    border-bottom-right-radius: 14px;
    top: -1px;
}
QTabWidget#panel-tabs > QWidget {
    background: transparent;
}
QTabBar::tab {
    background: rgba(241, 246, 255, 0.98);
    color: #5676a1;
    border: 1px solid rgba(132, 162, 210, 0.20);
    border-bottom: none;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    padding: 8px 14px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background: rgba(247, 250, 255, 0.98);
    color: #21334d;
}
QTabBar::tab:!selected {
    margin-top: 3px;
}
QLineEdit, QSpinBox, QComboBox, QTableWidget, QTableView, QTreeView, QProgressBar {
    background: rgba(255, 255, 255, 0.98);
    border: 1px solid rgba(132, 162, 210, 0.34);
    border-radius: 10px;
    padding: 6px 8px;
    selection-background-color: rgba(99, 158, 255, 0.22);
    selection-color: #1f3150;
}
QComboBox, QSpinBox {
    min-height: 22px;
    padding-right: 28px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    border-left: 1px solid rgba(132, 162, 210, 0.22);
    border-top-right-radius: 10px;
    border-bottom-right-radius: 10px;
    background: rgba(241, 246, 255, 0.92);
}
QComboBox::down-arrow {
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    background: rgba(255, 255, 255, 0.99);
    border: 1px solid rgba(132, 162, 210, 0.30);
    border-radius: 10px;
    outline: 0;
    padding: 4px;
    selection-background-color: rgba(99, 158, 255, 0.18);
    selection-color: #1f3150;
}
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border;
    width: 24px;
    border: none;
    border-left: 1px solid rgba(132, 162, 210, 0.22);
    background: rgba(241, 246, 255, 0.92);
}
QSpinBox::up-button {
    subcontrol-position: top right;
    border-top-right-radius: 10px;
}
QSpinBox::down-button {
    subcontrol-position: bottom right;
    border-top: 1px solid rgba(132, 162, 210, 0.16);
    border-bottom-right-radius: 10px;
}
QSpinBox::up-arrow {
    width: 10px;
    height: 10px;
}
QSpinBox::down-arrow {
    width: 10px;
    height: 10px;
}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {
    border-color: rgba(92, 145, 224, 0.52);
}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {
    border: 1px solid rgba(76, 137, 226, 0.78);
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
QFrame#vedo-shell {
    background: rgba(255, 255, 255, 0.98);
    border: 1px solid rgba(132, 162, 210, 0.20);
    border-radius: 0px;
}
"""


def _asset_url(filename: str) -> str:
    return asset_path(filename).as_posix()


def build_app_qss() -> str:
    combo_arrow = _asset_url("combo-arrow-down.svg")
    spin_up_arrow = _asset_url("spin-arrow-up.svg")
    spin_down_arrow = _asset_url("spin-arrow-down.svg")
    return (
        APP_QSS
        + f"""
QComboBox::down-arrow {{
    image: url("{combo_arrow}");
}}
QSpinBox::up-arrow {{
    image: url("{spin_up_arrow}");
}}
QSpinBox::down-arrow {{
    image: url("{spin_down_arrow}");
}}
"""
    )
