"""Run after starting the real app container in CI, on each supported CPU."""
import json
from pathlib import Path
import time
from urllib.error import URLError
from urllib.request import urlopen

version = (Path(__file__).resolve().parents[1]/"VERSION").read_text().strip()
for attempt in range(45):
    try:
        with urlopen("http://127.0.0.1:18099/health", timeout=2) as response:
            data = json.load(response)
        if data != {"status": "ok", "version": version}:
            raise RuntimeError(f"Unexpected app response: {data}")
        print(f"App container started successfully: V{version}")
        break
    except (URLError, TimeoutError, ConnectionError):
        time.sleep(1)
else:
    raise RuntimeError("App container did not become healthy")
