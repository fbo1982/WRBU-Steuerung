"""Category validation shared by the UI and runtime. No HA dependency."""
from copy import deepcopy
from .events import LABELS, matches, validate_event_rules


def validate_categories(categories: list[dict]) -> list[dict]:
    """Reject overlapping months, duplicate names and invalid SOC values."""
    result = deepcopy(categories)
    used = set()
    names = set()
    for category in result:
        name = str(category.get("name", "")).strip()
        months = category.get("months", [])
        if not name or name.casefold() in names:
            raise ValueError("Name fehlt oder ist bereits vergeben")
        if not months or len(set(months)) != len(months):
            raise ValueError("Mindestens einen eindeutigen Monat auswählen")
        if any(type(m) is not int or not 1 <= m <= 12 for m in months):
            raise ValueError("Ungültiger Monat")
        if used.intersection(months):
            raise ValueError("Ein Monat gehört bereits zu einer anderen Kategorie")
        for key in ("normal_soc", "warning_soc"):
            value = category.get(key)
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("SOC muss eine ganze Zahl zwischen 0 und 100 sein")
        if category["warning_soc"] < category["normal_soc"]:
            raise ValueError("Die Warnreserve darf die normale Reserve nicht unterschreiten")
        if "event_rules" in category:
            category["event_rules"] = validate_event_rules(category["event_rules"], category["normal_soc"])
        category["name"] = name
        category["months"] = sorted(months)
        used.update(months)
        names.add(name.casefold())
    return result


def available_months(categories: list[dict], editing: int | None = None) -> list[int]:
    """Already assigned months cannot even be selected in a second category."""
    used = {m for i, c in enumerate(categories) if i != editing for m in c["months"]}
    return [m for m in range(1, 13) if m not in used]


def decision_for(categories: list[dict], month: int, level: str | None, warnings=None):
    """Unknown weather or an unassigned month means hold current reserve."""
    if level not in ("0", "1", "2", "3", "4"):
        return {"desired": None, "category": None, "reason": "Wetterstandort/DWD-Daten fehlen oder sind veraltet."}
    category = next((c for c in categories if month in c["months"]), None)
    if category is None:
        return {"desired": None, "category": None, "reason": "Der aktuelle Monat hat keine Kategorie."}
    rules = category.get("event_rules", [])
    if not rules:
        key = "warning_soc" if int(level) >= 2 else "normal_soc"
        return {"desired": category[key], "category": category["name"],
                "reason": "DWD-Standardreserve (Stufe 2–4)" if key == "warning_soc" else "Normale Monatsreserve"}
    valid_warnings = isinstance(warnings, list) and all(
        isinstance(w, dict) and type(w.get("level")) is int and 1 <= w["level"] <= 4
        and type(w.get("event_code")) is int and 1 <= w["event_code"] <= 999
        and isinstance(w.get("groups"), list) and bool(w["groups"])
        and all(isinstance(g, str) and bool(g) for g in w["groups"])
        for w in warnings)
    if warnings is None and level == "0":
        warnings, valid_warnings = [], True
    if not valid_warnings or max((w["level"] for w in warnings), default=0) != int(level):
        return {"desired": None, "category": category["name"], "reason": "DWD-Ereignisdaten fehlen; Reserve bleibt unverändert."}
    contributions = [(category["normal_soc"], "Normale Monatsreserve")]
    for warning in warnings or []:
        matching = [r for r in rules if matches(r, warning)]
        if matching:
            contributions.extend((r["soc"], LABELS[r["event"]]+f" · ab Stufe {r['min_level']}") for r in matching)
        elif warning["level"] >= 2:
            contributions.append((category["warning_soc"], "DWD-Standardreserve: "+warning.get("event", "Unbekanntes Ereignis")))
    desired = max(value for value, _ in contributions)
    reasons = list(dict.fromkeys(reason for value, reason in contributions if value == desired))
    return {"desired": desired, "category": category["name"], "reason": "; ".join(reasons[:3])}


def target_for(categories: list[dict], month: int, level: str | None, warnings=None):
    decision = decision_for(categories, month, level, warnings)
    return decision["desired"], decision["category"]

