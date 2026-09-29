"""Readable list of what changed between two sets of connections.

Connections are grouped back into datasets by name ("WMS UK EA: Flood Risk
Areas" and "WFS UK EA: Flood Risk Areas" are one dataset), so a changed link
is one "Updated" row instead of a removed and an added row of URLs.
"""
import csv

from .sources import MAP_TYPES, regions as default_regions

CHANGE_ORDER = {"New": 0, "Updated": 1, "Renamed": 2, "Retired": 3, "Reinstated": 4, "Removed": 5}
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


def _norm(title):
    """Match key for a title: untidy old names (extra spaces, "/") match their tidy version."""
    return " ".join(title.replace("/", "-").replace("\\", "-").split()).casefold()


def datasets(conns, regions):
    """{key: {region, publisher, title, retired, services: {type: url}, conns: [...]}}."""
    out = {}
    for c in conns:
        service, region, publisher, title, retired = split_name(c["name"], regions)
        key = (region, publisher.strip(), _norm(title))
        ds = out.setdefault(key, {"region": region, "publisher": publisher.strip(),
                                  "title": " ".join(title.split()), "retired": retired,
                                  "services": {}, "source": c.get("source", ""), "conns": []})
        ds["services"][service] = c["attrs"].get("url", "")
        ds["conns"].append(c)
    return out


def _order(services):
    known = [s for s in MAP_TYPES if s in services]
    return known + sorted(s for s in services if s not in MAP_TYPES)


def _row(change, ds, services, details, old=None, new=None):
    """A change row. "old" / "new": the connections to remove / write to apply it
    (only for the Browser comparison); "key" identifies it across runs (ticks)."""
    return {"Change": change, "Region": ds["region"], "Publisher": ds["publisher"],
            "Dataset": ds["title"], "Services": services, "Source": ds["source"],
            "details": details, "old": (old or {}).get("conns", []), "new": (new or {}).get("conns", []),
            "key": f"{change}|{ds['region']}|{ds['publisher']}|{_norm(ds['title'])}"}


def _links(ds):
    return "\n".join(f"{s}: {ds['services'][s]}" for s in _order(ds["services"]))


def diff(old_conns, new_conns, regions=None):
    """Rows of Change, Region, Publisher, Dataset, Services, Source, plus details (links, for
    tooltips), old / new (connections to remove / write) and key.

    regions: name prefix -> region ("UK" -> "England"); default from the sources list.
    """
    regions = regions or default_regions()
    old, new = datasets(old_conns or [], regions), datasets(new_conns or [], regions)
    rows = []
    for key, ds in new.items():
        if key not in old:
            rows.append(_row("New", ds, ", ".join(_order(ds["services"])), _links(ds), new=ds))
            continue
        before = old[key]
        parts = [f"+{s}" for s in _order(ds["services"]) if s not in before["services"]]
        parts += [f"-{s}" for s in _order(before["services"]) if s not in ds["services"]]
        parts += [f"{s} link changed" for s in _order(ds["services"])
                  if s in before["services"] and before["services"][s] != ds["services"][s]]
        # Same dataset under other names (old script: extra spaces, "/"; or a duplicate).
        new_names = {(c["kind"], c["name"]) for c in ds["conns"]}
        extra = sorted(c["name"] for c in before["conns"] if (c["kind"], c["name"]) not in new_names)
        if ds["retired"] != before["retired"]:
            rows.append(_row("Retired" if ds["retired"] else "Reinstated", ds, ", ".join(parts),
                             _links(ds), before, ds))
        elif parts:
            if extra:
                parts.append("old name replaced")
            rows.append(_row("Updated", ds, ", ".join(parts), _links(ds), before, ds))
        elif extra:
            rows.append(_row("Renamed", ds, "replaces " + ", ".join(f"'{n}'" for n in extra),
                             _links(ds), before, ds))
    for key, ds in old.items():
        if key not in new:
            rows.append(_row("Removed", ds, ", ".join(_order(ds["services"])), _links(ds), old=ds))
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
