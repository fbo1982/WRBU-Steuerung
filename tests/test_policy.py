from copy import deepcopy
import unittest
from harness import CATEGORIES, policy


class PolicyTests(unittest.TestCase):
    def test_categories_select_different_warning_reserves(self):
        self.assertEqual(policy.target_for(CATEGORIES, 1, "2"), (70, "Winter"))
        self.assertEqual(policy.target_for(CATEGORIES, 6, "4"), (60, "Rest"))
        self.assertEqual(policy.target_for(CATEGORIES, 6, "1"), (25, "Rest"))

    def test_no_duplicate_months(self):
        data = deepcopy(CATEGORIES)
        data[1]["months"].append(12)
        with self.assertRaises(ValueError):
            policy.validate_categories(data)

    def test_edit_keeps_own_months_available(self):
        self.assertEqual(policy.available_months(CATEGORIES), [])
        self.assertEqual(policy.available_months(CATEGORIES, 0), [1, 12])

    def test_delete_frees_months(self):
        self.assertEqual(policy.available_months(CATEGORIES[1:]), [1, 12])

    def test_unknown_weather_and_unassigned_months_hold(self):
        for level in (None, "unknown", "unavailable", "NaN", "5"):
            self.assertEqual(policy.target_for(CATEGORIES, 1, level), (None, None))
        self.assertEqual(policy.target_for(CATEGORIES[:1], 6, "3"), (None, None))

    def test_bad_soc_names_and_months_rejected(self):
        for key, value in [("months", []), ("months", [0]), ("months", [13]),
                           ("months", [1, 1]), ("normal_soc", -1),
                           ("warning_soc", 101), ("normal_soc", 40.5),
                           ("warning_soc", 20), ("name", " ")]:
            data = deepcopy(CATEGORIES[:1])
            data[0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                policy.validate_categories(data)
        data = deepcopy(CATEGORIES)
        data[1]["name"] = " WINTER "
        with self.assertRaises(ValueError):
            policy.validate_categories(data)

