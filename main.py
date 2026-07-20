import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

import uvicorn
from src.web.server import app

uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
