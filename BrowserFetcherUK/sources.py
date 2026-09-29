"""Catalogue sources, built from an editable list (sources.json in the QGIS profile).

Types:
  ckan        CKAN package_search (data.gov.uk), filtered to organisations. Fast.
  defra       DEFRA Data Services Platform catalogue API. Complete for DEFRA
              bodies (incl. MMO) but very slow: 75-100 s per page of 200.
  geonetwork  GeoNetwork Elasticsearch search API (Spatial Data Scotland, NRW).

Each source fetches one page of raw records at a time and turns them into
small records {"id", "name": "UK EA: Flood Risk Areas", "services": {"WMS": url}}.
Only records with at least one map service are kept.

"use" says which fetch runs a source: "quick" (Fetch latest data), "full"
(Full fetch) or "both". Sources with the same "slot" replace each other: the
England slot comes from CKAN in a quick fetch and from DEFRA in a full one.
"""
import copy
import json
import os
import re
from urllib.parse import urlencode, urlparse, parse_qs

from .network import request

PAGE_SIZE = 200         # DEFRA and GeoNetwork page size, as tuned in filter_v07.py
CKAN_PAGE_SIZE = 1000   # CKAN maximum
TIMEOUT = 300           # seconds per page; DEFRA pages take 75-100 s
MAX_RETRIES = 10
BASE_SLEEP = 1          # pause between pages, and first retry wait
SLEEP_INCREMENT = 30    # each further retry waits this much longer

# Connection types that become browser connections, in the order they are written.
MAP_TYPES = ["WMS", "WMTS", "WFS", "OGC", "WCS", "XYZ", "VT"]
TYPES = ["ckan", "defra", "geonetwork"]
USES = ["quick", "full", "both"]

DEFRA_ORGS = ["department-for-environment-food-and-rural-affairs", "environment-agency",
              "forestry-commission", "marine-management-organisation", "natural-england",
              "rural-payments-agency"]

DEFAULTS = [
    {"key": "england_ckan", "enabled": True, "label": "England (data.gov.uk)", "type": "ckan",
     "use": "quick", "slot": "england", "prefix": "UK", "region": "England",
     "url": "https://ckan.publishing.service.gov.uk/api/action/package_search",
     "organisations": DEFRA_ORGS},
    {"key": "defra", "enabled": True, "label": "England (DEFRA catalogue)", "type": "defra",
     "use": "full", "slot": "england", "prefix": "UK", "region": "England",
     "url": "https://environment.data.gov.uk/backend/catalog/api/catalog/data-sets"},
    {"key": "sepa", "enabled": True, "label": "Scotland (Spatial Data Scotland)", "type": "geonetwork",
     "use": "both", "slot": "scotland", "prefix": "Scot", "region": "Scotland",
     "url": "https://spatialdata.gov.scot/geonetwork/srv/api/search/records/_search",
     "default_org": "Scottish Environment Protection Agency"},
    {"key": "nrw", "enabled": True, "label": "Wales (NRW)", "type": "geonetwork",
     "use": "both", "slot": "wales", "prefix": "Wales", "region": "Wales",
     "url": "https://metadata.naturalresources.wales/geonetwork/srv/api/search/records/_search",
     "default_org": "Natural Resources Wales", "infer_wfs": True},
]

_SKIP_WORDS = {"and", "for", "the", "of", "in", "&"}
# Organisation names whose initials would come out wrong.
ORG_ALIASES = {"NatureScot": "NS", "NRW": "NRW"}


def detect_link_type(url):
    """Service type of a catalogue link: WMS, WFS, OGC, WCS, WMTS, XYZ, VT, ArcGIS, Download or Other."""
    if not isinstance(url, str):
        return "Other"

    parsed = urlparse(url.lower())
    query = parse_qs(parsed.query)
    path = parsed.path

    # Explicit query parameters (e.g. ?service=WMS)
    for key in ["service", "ows_service"]:
        if key in query:
            svc = query[key][0].lower()
            if svc in ["wms", "wfs", "wcs", "wmts"]:
                return svc.upper()

    if "/wms" in path:
        return "WMS"
    if "/wfs" in path:
        return "WFS"
    if "/ogc/features/v1" in path:
        return "OGC"
    if "=wcs" in url.lower() or "/wcs" in path:
        return "WCS"
    if "=wmts" in url.lower() or "/wmts" in path:
        return "WMTS"

    if re.search(r"\{z\}", url, re.IGNORECASE):
        if ".pbf" in url or "/tile/" in path:
            return "VT"
        return "XYZ"

    if "/arcgis/rest/services/" in path:
        return "ArcGIS"
    if "download=true" in url.lower():
        return "Download"
    return "Other"


def services(links):
    """{type: url} of the map service links; a later link of the same type wins (as in filter_v07)."""
    found = {}
    for link in links:
        if not isinstance(link, str) or not link.strip():
            continue
        link = link.strip()
        kind = detect_link_type(link)
        if kind in MAP_TYPES:
            found[kind] = link
    return {kind: found[kind] for kind in MAP_TYPES if kind in found}


def initials(creator):
    if not isinstance(creator, str) or not creator.strip():
        return "NA"
    if creator in ORG_ALIASES:
        return ORG_ALIASES[creator]
    creator = re.sub(r"\s*\(.*?\)", "", creator)
    return "".join(w[0].upper() for w in creator.split(" ") if w and w.lower() not in _SKIP_WORDS) or "NA"


def _as_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _retired(title, description):
    return ("[RETIRED] " if "retired" in (description or "").lower() else "") + (title or "")


class Source:
    """One catalogue, from a config dict (see DEFAULTS)."""
    page_size = PAGE_SIZE

    def __init__(self, config):
        self.config = config
        self.KEY = config["key"]
        self.LABEL = config.get("label") or self.KEY
        self.url = config["url"]
        self.prefix = config.get("prefix", "")
        self.slot = config.get("slot") or self.KEY

    def fetch_page(self, offset, feedback):
        """Return (total record count or None, raw records) for one page."""
        raise NotImplementedError

    def parse(self, raw):
        """Raw records to small records with map services (others dropped)."""
        out = []
        for item in raw:
            rec = self.parse_one(item)
            if rec and rec["services"]:
                out.append(rec)
        return out

    def parse_one(self, item):
        raise NotImplementedError

    def name(self, creator, title):
        return f"{self.prefix} {initials(creator)}: {title}".strip()


class CkanSource(Source):
    page_size = CKAN_PAGE_SIZE

    def fetch_page(self, offset, feedback):
        params = {"rows": self.page_size, "start": offset, "sort": "id asc"}
        orgs = self.config.get("organisations") or []
        if orgs:
            params["fq"] = "organization:(" + " OR ".join(orgs) + ")"
        body = request(f"{self.url}?{urlencode(params)}", feedback=feedback, timeout=TIMEOUT,
                       headers={"Accept": "application/json"})
        result = json.loads(body).get("result", {})
        return result.get("count"), result.get("results", []) or []

    def parse_one(self, pkg):
        creator = (pkg.get("organization") or {}).get("title", "")
        links = [r.get("url") for r in pkg.get("resources") or [] if isinstance(r, dict)]
        title = _retired(pkg.get("title"), pkg.get("notes"))
        return {"id": pkg.get("id") or title, "name": self.name(creator, title),
                "services": services(links)}


class DefraSource(Source):
    def fetch_page(self, offset, feedback):
        params = {"exchange": "defra", "showRetired": "false", "showArchived": "false",
                  "searchType": "all", "limit": self.page_size, "sort": "title",
                  "exportFileFormat": "defra", "offset": offset}
        body = request(f"{self.url}?{urlencode(params)}", feedback=feedback, timeout=TIMEOUT,
                       headers={"Accept": "application/json"})
        data = json.loads(body)
        return data.get("count"), data.get("dataSets", []) or []

    def parse_one(self, ds):
        title = _retired(ds.get("title"), ds.get("description"))
        links = [res.get("url") for res in ds.get("resources") or [] if isinstance(res, dict)]
        return {"id": ds.get("id") or title, "name": self.name(ds.get("creator") or "", title),
                "services": services(links)}


class GeonetworkSource(Source):
    """Elasticsearch search API of a GeoNetwork 4 catalogue."""

    def fetch_page(self, offset, feedback):
        payload = {"query": {"match_all": {}}, "from": offset, "size": self.page_size}
        body = request(self.url, data=json.dumps(payload).encode(), feedback=feedback,
                       timeout=TIMEOUT, headers={"Accept": "application/json"})
        hits = json.loads(body).get("hits", {})
        total = hits.get("total", {})
        total = total.get("value") if isinstance(total, dict) else total
        return total, hits.get("hits", []) or []

    def links(self, links):
        if not self.config.get("infer_wfs"):
            return links
        # DataMapWales publishes only WMS links; the matching WFS nearly always exists.
        extra = [link.lower().replace("ows_service=wms", "ows_service=wfs")
                 for link in links
                 if isinstance(link, str) and "datamap.gov.wales" in link
                 and "ows_service=wms" in link.lower()]
        # Sorted so the same links always win (filter_v07 used an unordered set).
        return sorted({link for link in links + extra if isinstance(link, str)})

    def parse_one(self, hit):
        source = hit.get("_source", {})
        default_org = self.config.get("default_org", "")
        title_obj = source.get("resourceTitleObject", {})
        title = title_obj.get("default", "Unknown Title") if isinstance(title_obj, dict) else "Unknown Title"
        org = source.get("Org", default_org)
        creator_obj = source.get("OrgObject", {})
        creator = (creator_obj.get("default", default_org)
                   if isinstance(creator_obj, dict) and creator_obj else org)
        links = self.links(_as_list(source.get("linkUrl")))
        return {"id": hit.get("_id") or title, "name": self.name(creator, title),
                "services": services(links)}


CLASSES = {"ckan": CkanSource, "defra": DefraSource, "geonetwork": GeonetworkSource}


# ------------------------------------------------------------------- config
def config_path():
    from .store import PROFILE_DIR
    return os.path.join(PROFILE_DIR, "sources.json")


def validate(configs):
    """Checked copy of a list of source configs; raises ValueError with a readable message."""
    if not isinstance(configs, list):
        raise ValueError("expected a list of sources")
    out, keys = [], set()
    for i, c in enumerate(configs, 1):
        if not isinstance(c, dict):
            raise ValueError(f"source {i}: not an object")
        c = dict(c)
        c["key"] = re.sub(r"[^A-Za-z0-9_-]", "_", str(c.get("key") or c.get("label") or f"source{i}"))
        if c["key"] in keys:
            raise ValueError(f"source {i}: key '{c['key']}' used twice")
        keys.add(c["key"])
        if c.get("type") not in CLASSES:
            raise ValueError(f"source {c['key']}: type must be one of {', '.join(TYPES)}")
        if c.get("use", "both") not in USES:
            raise ValueError(f"source {c['key']}: use must be one of {', '.join(USES)}")
        if not str(c.get("url", "")).startswith(("http://", "https://")):
            raise ValueError(f"source {c['key']}: url must start with http:// or https://")
        c.setdefault("use", "both")
        c.setdefault("enabled", True)
        c.setdefault("label", c["key"])
        c.setdefault("prefix", "")
        c.setdefault("region", "Other")
        c.setdefault("slot", c["key"])
        if isinstance(c.get("organisations"), str):
            c["organisations"] = [o.strip() for o in c["organisations"].split(",") if o.strip()]
        out.append(c)
    return out


def load_config():
    from .store import read_json
    data = read_json(config_path())
    if data is None:
        return copy.deepcopy(DEFAULTS)
    try:
        return validate(data)
    except ValueError:
        return copy.deepcopy(DEFAULTS)


def save_config(configs):
    from .store import write_json
    write_json(config_path(), validate(configs), indent=1)


def reset_config():
    try:
        os.remove(config_path())
    except OSError:
        pass


def for_fetch(full, configs=None):
    """Source objects for a quick (full=False) or full fetch."""
    wanted = ("full", "both") if full else ("quick", "both")
    return [CLASSES[c["type"]](c) for c in (configs or load_config())
            if c.get("enabled", True) and c.get("use", "both") in wanted]


def regions(configs=None):
    """Name prefix -> region, for the change list."""
    out = {"UK": "England", "Scot": "Scotland", "Wales": "Wales"}
    for c in configs or load_config():
        if c.get("prefix"):
            out[c["prefix"]] = c.get("region") or "Other"
    return out
