# Browser Fetcher UK

QGIS plugin that fills the QGIS Browser with UK government map services: every dataset with a WMS,
WMTS, WFS, OGC API Features, WCS, XYZ or vector tile service in the catalogues of

- **England**: data.gov.uk (fast fetch), or the complete DEFRA Data Services Platform catalogue (slow fetch)
- **Scotland**: Spatial Data Scotland
- **Wales**: Natural Resources Wales

plus built-in BGS, UKCEH, OpenStreetMap and Mapzen connections and any connections of your own.
About 3,000 connections in all. QGIS 3.30 or later, including QGIS 4.

## Install

Add the plugin repository `https://ZedeN1.github.io/qgis-plugins/plugins.xml` in
**Plugins > Manage and Install Plugins > Settings**, then install **Browser Fetcher UK**.
Open it from the toolbar or **Web > Browser Fetcher UK**.

## Use

1. **Fast fetch** (under a minute) or **Slow fetch** (an hour or more) saves a new copy of the
   connection list. Fetching never changes your QGIS Browser.
2. The **Changes** list shows what applying the copy would change: new, removed, updated and retired
   datasets, with the source of each.
3. **Apply to QGIS Browser** writes the copy to the Browser. No downloading: it reads the copy in the
   shared or local folder. Only connections the plugin added are replaced; yours are never touched.

| Fetch | England from | Notes |
|---|---|---|
| Fast fetch | data.gov.uk | Lacks some DEFRA datasets (mostly Marine Management Organisation) and lists some retired services. Datasets found only by the last slow fetch are kept, so they never show as removed. |
| Slow fetch | the complete DEFRA catalogue | Each page of 200 datasets takes over a minute. Progress is saved after every page, so it resumes where it stopped. |

- Fast fetch reuses a copy younger than 24 hours instead of fetching again, and suggests a slow fetch
  once the last one is more than 30 days old (both adjustable in Settings; 0 switches them off).
- The first Apply offers to remove connections left by the old `AddConnectionsToQGIS.py` script.
- Earlier fetches and applies are kept in the history and can be saved as CSV.

### Shared folder (teams)

**EVY staff** (next to the status) is ticked by default; untick it if you are not Edenvale Young
staff, and the choice is remembered. Or point **Settings > Shared folder** at any network folder. One
person fetches, and everyone else only needs **Apply to QGIS Browser**. An update lock stops two people fetching into the same folder at once; a run
that stopped without finishing (QGIS closed, laptop asleep) can be resumed by anyone after 15 minutes.
When the shared folder cannot be reached, the plugin uses the local folder. Back on the network, the
newer of the two copies is used. A newer local copy from a complete slow fetch can be shared with
**Copy slow fetch to shared folder** (after a preview of what changes for everyone); a slow fetch
interrupted off the network resumes in the local folder. Nothing is copied automatically.

EVY staff mode also adds Ordnance Survey connections through the EVY Niagara proxy, which only work on
the EVY VPN.

### Custom connections and catalogues

- **Settings > Custom connections**: one list of the built-in and your own connections, each with a tick
  box to choose which are used. Add your own (import QGIS connection XML files, or pick connections
  already in your Browser) and export any of them to share.
- **Settings > Catalogue sources**: the catalogues searched (CKAN, DEFRA or GeoNetwork 4 APIs), their
  URLs, connection name prefixes and whether the fast or slow fetch uses them. Import / export as JSON.
- **Export XML** saves every connection as QGIS XML files.

## Files

| Where | What |
|---|---|
| Data folder (shared or local) | `snapshot.json` (current list), `history/` (earlier lists and change CSVs), `work/` (unfinished fetch), `update.lock`, `update.log` |
| QGIS profile `BrowserFetcherUK/` | `applied.json` (what your Browser has), `owned.json` (connections the plugin added), `custom_connections.json`, `sources.json` |

## Issues

https://github.com/ZedeN1/BrowserFetcherUK/issues
