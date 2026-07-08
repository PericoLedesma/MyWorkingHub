import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

import threading
import webbrowser
import uvicorn
from src.web.server import app

if "--no-browser" not in sys.argv:
    def _open_browser():
        import time; time.sleep(0.8)
        webbrowser.open("http://127.0.0.1:8765")
    threading.Thread(target=_open_browser, daemon=True).start()

uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
