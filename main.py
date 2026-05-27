import logging
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from src.app import SmartRecorderApp

_DARK_QSS = """
    QWidget { font-family: 'Segoe UI', sans-serif; }

    QScrollBar:vertical {
        background: #111113; width: 6px; border: none; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #3f3f46; border-radius: 3px; min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #52525b; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

    QScrollBar:horizontal {
        background: #111113; height: 6px; border: none; margin: 0;
    }
    QScrollBar::handle:horizontal {
        background: #3f3f46; border-radius: 3px; min-width: 20px;
    }
    QScrollBar::handle:horizontal:hover { background: #52525b; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

    QComboBox {
        background: #27272a; color: #fafafa;
        border: 1px solid #3f3f46; border-radius: 5px;
        padding: 3px 8px; font-size: 11px;
    }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox::down-arrow {
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #71717a;
        width: 0; height: 0;
    }
    QComboBox QAbstractItemView {
        background: #27272a; color: #fafafa;
        selection-background-color: #312e81;
        selection-color: #c7d2fe;
        border: 1px solid #3f3f46; outline: none;
    }

    QToolTip {
        background: #27272a; color: #fafafa;
        border: 1px solid #3f3f46; padding: 4px; border-radius: 4px;
    }

    QMenuBar {
        background: #111113; color: #fafafa;
        border-bottom: 1px solid #27272a;
        padding: 2px 0;
    }
    QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 4px; }
    QMenuBar::item:selected { background: #27272a; }
    QMenuBar::item:pressed  { background: #312e81; }

    QMenu {
        background: #18181b; color: #fafafa;
        border: 1px solid #3f3f46; padding: 4px;
    }
    QMenu::item { padding: 6px 28px 6px 12px; border-radius: 4px; }
    QMenu::item:selected { background: #312e81; color: #c7d2fe; }
    QMenu::item:disabled { color: #52525b; }
    QMenu::separator { height: 1px; background: #3f3f46; margin: 4px 8px; }
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window,          QColor("#09090b"))
    p.setColor(QPalette.WindowText,      QColor("#fafafa"))
    p.setColor(QPalette.Base,            QColor("#27272a"))
    p.setColor(QPalette.AlternateBase,   QColor("#18181b"))
    p.setColor(QPalette.ToolTipBase,     QColor("#27272a"))
    p.setColor(QPalette.ToolTipText,     QColor("#fafafa"))
    p.setColor(QPalette.Text,            QColor("#fafafa"))
    p.setColor(QPalette.Button,          QColor("#27272a"))
    p.setColor(QPalette.ButtonText,      QColor("#fafafa"))
    p.setColor(QPalette.BrightText,      QColor("white"))
    p.setColor(QPalette.Link,            QColor("#6366f1"))
    p.setColor(QPalette.Highlight,       QColor("#6366f1"))
    p.setColor(QPalette.HighlightedText, QColor("white"))
    p.setColor(QPalette.PlaceholderText, QColor("#52525b"))
    return p


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(_DARK_QSS)

    window = SmartRecorderApp()
    window.show()
    sys.exit(app.exec())
