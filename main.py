import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

if "--pyside" not in sys.argv:
    # Web UI (default — use --pyside to launch the legacy PySide6 window)
    import threading
    import webbrowser
    import uvicorn
    from src.web.server import app

    def _open_browser():
        import time; time.sleep(0.8)
        webbrowser.open("http://127.0.0.1:8765")

    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")

else:
    # Legacy PySide6 UI — python main.py --pyside
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
    from src.app import SmartRecorderApp

    _DARK_QSS = """
        QWidget { font-family: 'Segoe UI', sans-serif; }
        QScrollBar:vertical { background:#111113; width:6px; border:none; margin:0; }
        QScrollBar::handle:vertical { background:#3f3f46; border-radius:3px; min-height:20px; }
        QScrollBar::handle:vertical:hover { background:#52525b; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:none; }
        QComboBox { background:#27272a; color:#fafafa; border:1px solid #3f3f46; border-radius:5px; padding:3px 8px; font-size:11px; }
        QComboBox::drop-down { border:none; width:20px; }
        QComboBox::down-arrow { border-left:4px solid transparent; border-right:4px solid transparent; border-top:5px solid #71717a; width:0; height:0; }
        QComboBox QAbstractItemView { background:#27272a; color:#fafafa; selection-background-color:#312e81; selection-color:#c7d2fe; border:1px solid #3f3f46; outline:none; }
        QToolTip { background:#27272a; color:#fafafa; border:1px solid #3f3f46; padding:4px; border-radius:4px; }
    """

    def _dark_palette():
        p = QPalette()
        p.setColor(QPalette.Window,          QColor("#09090b"))
        p.setColor(QPalette.WindowText,      QColor("#fafafa"))
        p.setColor(QPalette.Base,            QColor("#27272a"))
        p.setColor(QPalette.AlternateBase,   QColor("#18181b"))
        p.setColor(QPalette.Text,            QColor("#fafafa"))
        p.setColor(QPalette.Button,          QColor("#27272a"))
        p.setColor(QPalette.ButtonText,      QColor("#fafafa"))
        p.setColor(QPalette.Highlight,       QColor("#6366f1"))
        p.setColor(QPalette.HighlightedText, QColor("white"))
        return p

    qt_app = QApplication(sys.argv)
    qt_app.setStyle("Fusion")
    qt_app.setPalette(_dark_palette())
    qt_app.setStyleSheet(_DARK_QSS)
    window = SmartRecorderApp()
    window.show()
    sys.exit(qt_app.exec())
