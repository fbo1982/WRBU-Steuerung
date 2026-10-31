"""Run after starting the real app container in CI, on each supported CPU."""
import json
import time
from urllib.error import URLError
from urllib.request import urlopen

for attempt in range(45):
    try:
        with urlopen("http://127.0.0.1:18099/health", timeout=2) as response:
            data = json.load(response)
        if data != {"status": "ok", "version": "0.1.0"}:
            raise RuntimeError(f"Unexpected app response: {data}")
        print("App container started successfully: V0.1.0")
        break
    except (URLError, TimeoutError, ConnectionError):
        time.sleep(1)
else:
    raise RuntimeError("App container did not become healthy")
