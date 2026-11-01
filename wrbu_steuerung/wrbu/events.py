"""DWD CAP v2.1.14 sections 3.1 and 3.3; match codes, never headline text.

Source: https://www.dwd.de/DE/leistungen/opendata/help/warnungen/cap_dwd_profile_de_pdf_2_1_14.pdf
Optional phenomena (for example tornado with a thunderstorm) are matched only
when the DWD actually supplies the corresponding EC_GROUP.
"""

GROUPS = {
    "THUNDERSTORM": "Gewitter", "WIND": "Wind / Sturm", "TORNADO": "Tornado",
    "RAIN": "Regen", "HAIL": "Hagel", "SNOWFALL": "Schneefall",
    "SNOWDRIFT": "Schneeverwehung", "SLIPPERINESS": "Glätte / Glatteis",
    "BLACK_ICE": "Glatteis", "RIME_ICE": "Raueis", "FROST": "Frost",
    "FOG": "Nebel", "THAW": "Tauwetter", "POWERLINEVIBRATION": "Leiterseilschwingungen",
    "HEAT": "Hitze", "UV": "UV-Strahlung",
}
EVENTS = {
    11: "Böen (Küste)", 12: "Wind (Küste)", 13: "Sturm (Küste)",
    14: "Starkwind (Hochsee)", 15: "Sturm (Hochsee)", 16: "Schwerer Sturm (Hochsee)",
    22: "Frost", 31: "Gewitter",
    33: "Starkes Gewitter (Böen)", 34: "Starkes Gewitter (Starkregen)",
    36: "Starkes Gewitter (Böen und Starkregen)", 38: "Starkes Gewitter (Böen, Starkregen und Hagel)",
    40: "Schweres Gewitter mit Orkanböen", 41: "Schweres Gewitter mit extremen Orkanböen",
    42: "Schweres Gewitter mit heftigem Starkregen",
    44: "Schweres Gewitter mit Orkanböen und heftigem Starkregen",
    45: "Schweres Gewitter mit extremen Orkanböen und heftigem Starkregen",
    46: "Schweres Gewitter mit heftigem Starkregen und Hagel",
    48: "Schweres Gewitter mit Orkanböen, heftigem Starkregen und Hagel",
    49: "Schweres Gewitter mit extremen Orkanböen, heftigem Starkregen und Hagel",
    51: "Windböen", 52: "Sturmböen", 53: "Schwere Sturmböen",
    54: "Orkanartige Böen", 55: "Orkanböen", 56: "Extreme Orkanböen",
    57: "Starkwind", 58: "Sturm", 59: "Nebel",
    61: "Starkregen", 62: "Heftiger Starkregen", 63: "Dauerregen",
    64: "Ergiebiger Dauerregen", 65: "Extrem ergiebiger Dauerregen", 66: "Extrem heftiger Starkregen",
    70: "Leichter Schneefall", 71: "Schneefall", 72: "Starker Schneefall", 73: "Extrem starker Schneefall",
    74: "Schneeverwehung", 75: "Starke Schneeverwehung", 76: "Extrem starke Schneeverwehung",
    79: "Leiterseilschwingungen", 82: "Strenger Frost", 84: "Glätte", 85: "Glatteis",
    86: "Extremes Glatteis", 87: "Markante Glätte", 88: "Tauwetter", 89: "Starkes Tauwetter",
    90: "Gewitter", 91: "Starkes Gewitter", 92: "Schweres Gewitter", 93: "Extremes Gewitter",
    95: "Schweres Gewitter mit extrem heftigem Starkregen und Hagel",
    96: "Extremes Gewitter mit Orkanböen, extrem heftigem Starkregen und Hagel",
    246: "UV-Index", 247: "Starke Hitze", 248: "Extreme Hitze",
}
CATALOG = [
    *({"id": "group:"+key, "label": label, "kind": "group"} for key, label in GROUPS.items()),
    *({"id": "ii:"+str(code), "label": label+f" (DWD {code})", "kind": "event"} for code, label in EVENTS.items()),
]
LABELS = {item["id"]: item["label"] for item in CATALOG}


def validate_event_rules(rules, normal_soc):
    if not isinstance(rules, list) or len(rules) > 32:
        raise ValueError("Maximal 32 DWD-Ereignisregeln je Kategorie anlegen.")
    result, used = [], set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("event"), str) or rule["event"] not in LABELS:
            raise ValueError("Ungültiges DWD-Ereignis.")
        level, soc = rule.get("min_level"), rule.get("soc")
        if type(level) is not int or not 1 <= level <= 4:
            raise ValueError("Die Mindestwarnstufe muss zwischen 1 und 4 liegen.")
        if type(soc) is not int or not normal_soc <= soc <= 100:
            raise ValueError("Ereignis-SOC muss zwischen normaler Monatsreserve und 100 % liegen.")
        key = (rule["event"], level)
        if key in used:
            raise ValueError("Dieses Ereignis mit dieser Mindestwarnstufe ist bereits vorhanden.")
        used.add(key)
        result.append({"event": rule["event"], "min_level": level, "soc": soc})
    return result


def matches(rule, warning):
    selector = rule["event"]
    return (warning["level"] >= rule["min_level"] and
            (selector == "ii:"+str(warning["event_code"]) or
             selector in {"group:"+group for group in warning["groups"]}))
