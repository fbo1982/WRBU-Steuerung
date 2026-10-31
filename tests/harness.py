"""Load the real app package; no mocked Home Assistant framework."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wrbu_steuerung"))
from wrbu import policy, profiles, modbus

CONNECTION = {"name": "WR", "profile": "sungrow_sh", "host": "127.0.0.1",
              "port": 502, "unit": 1, "warning_entity": "sensor.dwd_current_warning_level"}
CATEGORIES = [
    {"name": "Winter", "months": [1, 12], "normal_soc": 40, "warning_soc": 70},
    {"name": "Rest", "months": list(range(2, 12)), "normal_soc": 25, "warning_soc": 60},
]

