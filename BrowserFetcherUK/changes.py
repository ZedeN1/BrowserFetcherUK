"""Readable list of what changed between two sets of connections.

Connections are grouped back into datasets by name ("WMS UK EA: Flood Risk
Areas" and "WFS UK EA: Flood Risk Areas" are one dataset), so a changed link
is one "Updated" row instead of a removed and an added row of URLs.
"""
import csv

from .sources import MAP_TYPES, regions as default_regions

CHANGE_ORDER = {"New": 0, "Updated": 1, "Retired": 2, "Reinstated": 3, "Removed": 4}
COLUMNS = ["Change", "Region", "Publisher", "Dataset", "Services", "Source"]
RETIRED = "[RETIRED] "


def split_name(name, regions):
    """'WMS UK EA: [RETIRED] Title' -> ('WMS', 'England', 'EA', 'Title', True)."""
    service, _, rest = name.partition(" ")
    prefix, sep, title = rest.partition(": ")
    if not sep:
        prefix, title = "", rest
    first, _, publisher = prefix.partition(" ")
    if first in regions:
        region = regions[first]
    else:
        region, publisher = "Other", prefix
    retired = title.startswith(RETIRED)
    if retired:
        title = title[len(RETIRED):]
    return service, region, publisher, title, retired


def datasets(conns, regions):
    """{key: {region, publisher, title, retired, services: {type: url}}} from connections."""
    out = {}
    for c in conns:
        service, region, publisher, title, retired = split_name(c["name"], regions)
        key = (region, publisher, title.casefold())
        ds = out.setdefault(key, {"region": region, "publisher": publisher, "title": title,
                                  "retired": retired, "services": {}, "source": c.get("source", "")})
        ds["services"][service] = c["attrs"].get("url", "")
    return out


def _order(services):
    known = [s for s in MAP_TYPES if s in services]
    return known + sorted(s for s in services if s not in MAP_TYPES)


def _row(change, ds, services, details):
    return {"Change": change, "Region": ds["region"], "Publisher": ds["publisher"],
            "Dataset": ds["title"], "Services": services, "Source": ds["source"],
            "details": details}


def diff(old_conns, new_conns, regions=None):
    """Rows of Change, Region, Publisher, Dataset, Services (+ details: links, for tooltips).

    regions: name prefix -> region ("UK" -> "England"); default from the sources list.
    """
    regions = regions or default_regions()
    old, new = datasets(old_conns or [], regions), datasets(new_conns or [], regions)
    rows = []
    for key, ds in new.items():
        links = "\n".join(f"{s}: {ds['services'][s]}" for s in _order(ds["services"]))
        if key not in old:
            rows.append(_row("New", ds, ", ".join(_order(ds["services"])), links))
            continue
        before = old[key]
        parts = [f"+{s}" for s in _order(ds["services"]) if s not in before["services"]]
        parts += [f"-{s}" for s in _order(before["services"]) if s not in ds["services"]]
        parts += [f"{s} link changed" for s in _order(ds["services"])
                  if s in before["services"] and before["services"][s] != ds["services"][s]]
        if ds["retired"] != before["retired"]:
            rows.append(_row("Retired" if ds["retired"] else "Reinstated", ds, ", ".join(parts), links))
        elif parts:
            rows.append(_row("Updated", ds, ", ".join(parts), links))
    for key, ds in old.items():
        if key not in new:
            links = "\n".join(f"{s}: {ds['services'][s]}" for s in _order(ds["services"]))
            rows.append(_row("Removed", ds, ", ".join(_order(ds["services"])), links))
    rows.sort(key=lambda r: (CHANGE_ORDER[r["Change"]], r["Region"], r["Dataset"].casefold()))
    return rows


def counts(rows):
    out = {}
    for r in rows:
        out[r["Change"]] = out.get(r["Change"], 0) + 1
    return out


def summary(rows):
    if not rows:
        return "no changes"
    c = counts(rows)
    return ", ".join(f"{c[k]} {k.lower()}" for k in CHANGE_ORDER if k in c)


def write_csv(path, rows, date=None):
    """CSV with a Date column first (local date of the snapshot)."""
    if date:
        from .store import parse_time
        date = f"{parse_time(date).astimezone():%Y-%m-%d}"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Date"] + COLUMNS)
        for r in rows:
            writer.writerow([date or ""] + [r[c] for c in COLUMNS])


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [dict({c: "" for c in COLUMNS}, **r, details="") for r in csv.DictReader(f)]
