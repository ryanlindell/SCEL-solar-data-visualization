"""A tiny driver for a headless Chromium-based browser (Edge or Chrome), using the DevTools protocol.

It opens pages, runs JavaScript in them and takes screenshots - just enough to click buttons and drag
sliders in the real pages. It needs only the `websocket-client` package and an installed Edge/Chrome.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websocket import create_connection

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
]


def find_browser() -> str:
    """The browser to use: $BROWSER if set, else the first Edge/Chrome/Chromium found."""
    if os.environ.get("BROWSER"):
        return os.environ["BROWSER"]
    for candidate in BROWSER_CANDIDATES:
        if os.path.isfile(candidate) or shutil.which(candidate):
            return candidate
    raise RuntimeError("No Edge/Chrome/Chromium found. Install one, or set the BROWSER environment variable to its path.")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StaticServer:
    """Serves the web/ folder on a free local port (the pages cannot load their data from file://)."""

    def __init__(self):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(self.port), "--bind", "127.0.0.1", "--directory", str(WEB_DIR)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/index.html", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        self.close()
        raise RuntimeError("the local web server did not start")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.proc.kill()


class Browser:
    def __init__(self, width=1100, height=1400):
        self.width = width
        self.dir = tempfile.mkdtemp(prefix="cdp-")
        port = free_port()
        self.proc = subprocess.Popen(
            [find_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--remote-debugging-port={port}",
             "--remote-allow-origins=*", f"--user-data-dir={self.dir}", f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        page = None
        for _ in range(80):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1))
                page = next(p for p in pages if p["type"] == "page")
                break
            except Exception:
                time.sleep(0.25)
        if page is None:
            self.close()
            raise RuntimeError("the browser did not start")
        self.ws = create_connection(page["webSocketDebuggerUrl"], timeout=60)
        self.n = 0
        self.errors: list[str] = []
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(msg["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"]))
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expression):
        """Evaluate JavaScript in the page and return its (JSON-able) value. Promises are awaited."""
        result = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description", "JavaScript error"))
        return result["result"].get("value")

    def wait(self, condition, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(condition):
                    return
            except RuntimeError:
                pass
            time.sleep(0.2)
        raise TimeoutError(f"timed out waiting for: {condition}")

    def goto(self, url, ready="true", timeout=30):
        self.send("Page.navigate", url=url)
        self.wait(ready, timeout)

    def move_to(self, x, y):
        """Move the real mouse pointer to page coordinates (twice, so the page sees it arrive and settle)."""
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x - 2, y=y)
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def click_at(self, x, y):
        """A real mouse click at page coordinates (moves there first: Plotly only reacts to a click on a point it is hovering)."""
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def pause(self, ms=400):
        """Let animations and Plotly redraws finish."""
        self.js(f"new Promise(r => setTimeout(r, {ms}))")

    def screenshot(self, path, full_page=True):
        params = {"format": "png"}
        if full_page:
            height = self.js("document.documentElement.scrollHeight")
            params.update(captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": self.width, "height": height, "scale": 1})
        data = self.send("Page.captureScreenshot", **params)["data"]
        Path(path).write_bytes(base64.b64decode(data))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)
