import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from src.app import SmartRecorderApp

_DARK_QSS = """
    QWidget { font-family: 'Segoe UI', sans-serif; }

    QScrollBar:vertical {
        background: #1a1a1a; width: 8px; border: none; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #3a3a3a; border-radius: 4px; min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #4a4a4a; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

    QScrollBar:horizontal {
        background: #1a1a1a; height: 8px; border: none; margin: 0;
    }
    QScrollBar::handle:horizontal {
        background: #3a3a3a; border-radius: 4px; min-width: 20px;
    }
    QScrollBar::handle:horizontal:hover { background: #4a4a4a; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

    QComboBox {
        background: #2d2d2d; color: #e0e0e0;
        border: none; border-radius: 4px;
        padding: 4px 8px; font-size: 11px;
    }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox::down-arrow {
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #888;
        width: 0; height: 0;
    }
    QComboBox QAbstractItemView {
        background: #2d2d2d; color: #e0e0e0;
        selection-background-color: #2563eb;
        border: 1px solid #3a3a3a; outline: none;
    }

    QToolTip {
        background: #2d2d2d; color: #e0e0e0;
        border: 1px solid #444; padding: 4px;
    }
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window,          QColor("#1a1a1a"))
    p.setColor(QPalette.WindowText,      QColor("#e0e0e0"))
    p.setColor(QPalette.Base,            QColor("#2d2d2d"))
    p.setColor(QPalette.AlternateBase,   QColor("#242424"))
    p.setColor(QPalette.ToolTipBase,     QColor("#2d2d2d"))
    p.setColor(QPalette.ToolTipText,     QColor("#e0e0e0"))
    p.setColor(QPalette.Text,            QColor("#e0e0e0"))
    p.setColor(QPalette.Button,          QColor("#2d2d2d"))
    p.setColor(QPalette.ButtonText,      QColor("#e0e0e0"))
    p.setColor(QPalette.BrightText,      QColor("white"))
    p.setColor(QPalette.Link,            QColor("#2563eb"))
    p.setColor(QPalette.Highlight,       QColor("#2563eb"))
    p.setColor(QPalette.HighlightedText, QColor("white"))
    p.setColor(QPalette.PlaceholderText, QColor("#555"))
    return p


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(_DARK_QSS)

    window = SmartRecorderApp()
    window.show()
    sys.exit(app.exec())
