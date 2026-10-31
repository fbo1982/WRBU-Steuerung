"""Regression: weather fields must never block inverter communication tests."""
from html.parser import HTMLParser
from pathlib import Path
import unittest


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.controls = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = attrs["id"]
            self.controls[self.current] = []
        elif self.current and tag in ("input", "select", "button"):
            self.controls[self.current].append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class FormTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1]/"wrbu_steuerung/wrbu/static/index.html"
        self.parser = FormParser()
        self.parser.feed(source.read_text(encoding="utf-8"))

    def test_inverter_form_has_no_required_weather_control(self):
        controls = self.parser.controls["device-form"]
        required = {a.get("name", a.get("id")) for tag, a in controls if "required" in a}
        self.assertEqual(required, {"name", "profile", "host", "port", "unit"})
        self.assertFalse(any(a.get("name") == "warning_entity" for tag, a in controls))
        button = next(a for tag, a in controls if a.get("id") == "test-device")
        self.assertEqual(button["type"], "button")

    def test_weather_form_is_separate_and_empty_result_does_not_block_search(self):
        controls = self.parser.controls["weather-form"]
        region = next(a for tag, a in controls if a.get("id") == "weather-region")
        self.assertNotIn("required", region)
        self.assertTrue(any(a.get("id") == "weather-address" for tag, a in controls))
