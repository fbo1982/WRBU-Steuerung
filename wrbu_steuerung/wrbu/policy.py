"""Category validation shared by the UI and runtime. No HA dependency."""
from copy import deepcopy


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
        category["name"] = name
        category["months"] = sorted(months)
        used.update(months)
        names.add(name.casefold())
    return result


def available_months(categories: list[dict], editing: int | None = None) -> list[int]:
    """Already assigned months cannot even be selected in a second category."""
    used = {m for i, c in enumerate(categories) if i != editing for m in c["months"]}
    return [m for m in range(1, 13) if m not in used]


def target_for(categories: list[dict], month: int, level: str | None):
    """Unknown weather or an unassigned month means hold current reserve."""
    if level not in ("0", "1", "2", "3", "4"):
        return None, None
    category = next((c for c in categories if month in c["months"]), None)
    if category is None:
        return None, None
    key = "warning_soc" if int(level) >= 2 else "normal_soc"
    return category[key], category["name"]

