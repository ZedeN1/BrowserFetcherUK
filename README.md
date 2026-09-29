# Browser Fetcher UK

QGIS plugin that fills the QGIS Browser with UK government map services: every dataset with a WMS,
WMTS, WFS, OGC API Features, WCS, XYZ or vector tile service in the catalogues of

- **England**: data.gov.uk (fast), or the complete DEFRA Data Services Platform catalogue (full fetch)
- **Scotland**: Spatial Data Scotland
- **Wales**: Natural Resources Wales

plus built-in BGS, UKCEH, OpenStreetMap and Mapzen connections and any connections of your own.
About 3,000 connections in all. QGIS 3.30 or later, including QGIS 4.

## Install

Add the plugin repository `https://ZedeN1.github.io/qgis-plugins/plugins.xml` in
**Plugins > Manage and Install Plugins > Settings**, then install **Browser Fetcher UK**.
Open it from the toolbar or **Web > Browser Fetcher UK**.

## Use

| Button | What it does | Time |
|---|---|---|
| Update QGIS Browser | Replaces the connections the plugin added with the latest copy of the list | seconds |
| Fetch latest data | Searches the catalogues (England from data.gov.uk) and saves a new copy | under a minute |
| Full fetch | As above, but England from the complete DEFRA catalogue | an hour or more |

- Only connections the plugin added are removed on update. Your own connections are never touched.
- The first update offers to remove connections left by the old `AddConnectionsToQGIS.py` script.
- A full fetch finds datasets data.gov.uk lacks (mostly Marine Management Organisation). Later quick
  fetches keep those, so they do not show as removed. It also drops retired services that data.gov.uk
  still lists.
- Every page of a fetch is saved, so a cancelled fetch (or closed QGIS) resumes where it stopped.
- The **Changes** list shows what an update adds, removes or changes, with the source of each dataset.
  Earlier updates are kept in the history and can be saved as CSV.

### Shared folder (teams)

Tick **Settings > EVY staff** (ticked automatically when the Edenvale Young shared folder is found) or
point **Shared folder** at any network folder. One person fetches, and everyone else only needs
**Update QGIS Browser**. An update lock stops two people fetching into the same folder at once; a run
that stopped without finishing (QGIS closed, laptop asleep) can be resumed by anyone after 15 minutes.
When the shared folder cannot be reached, the plugin uses the local folder.

EVY staff mode also adds Ordnance Survey connections through the EVY Niagara proxy, which only work on
the EVY VPN.

### Custom connections and catalogues

- **Settings > Custom connections**: add your own connections (import QGIS connection XML files, or pick
  connections already in your Browser), export them to share, or switch off the built-in ones.
- **Settings > Catalogue sources**: the catalogues searched (CKAN, DEFRA or GeoNetwork 4 APIs), their
  URLs, connection name prefixes and whether the quick or full fetch uses them. Import / export as JSON.
- **Export XML** saves every connection as QGIS XML files.

## Files

| Where | What |
|---|---|
| Data folder (shared or local) | `snapshot.json` (current list), `history/` (earlier lists and change CSVs), `work/` (unfinished fetch), `update.lock`, `update.log` |
| QGIS profile `BrowserFetcherUK/` | `applied.json` (what your Browser has), `owned.json` (connections the plugin added), `custom_connections.json`, `sources.json` |

## Issues

https://github.com/ZedeN1/BrowserFetcherUK/issues
