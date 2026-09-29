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
2. The **Changes** list compares the copy with what is really in your Browser: new, updated,
   renamed, retired and removed datasets, with the source of each. Connections left by the old
   `AddConnectionsToQGIS.py` script are included (untidy old names show as Renamed, datasets no longer
   in the catalogues as Removed).
3. Tick the changes you want (tick box in the header corner, or Select None / New / Updated / Removed /
   Default) and click **Apply to QGIS Browser**. No downloading: it reads the copy in the shared or
   local folder. Your ticks are remembered: an unticked change stays unticked (shown in grey italics)
   until you tick it again. Default ticks everything except old-script-style connections whose URLs
   come from no catalogue the plugin knows, as those are probably your own.
4. Every Apply first saves a backup of all your Browser connections; **Settings > Restore backup**
   puts them back (last 10 kept). Connections with other names are never touched.

| Fetch | England from | Notes |
|---|---|---|
| Fast fetch | data.gov.uk | Lacks some DEFRA datasets (mostly Marine Management Organisation) and lists some retired services. Datasets found only by the last slow fetch are kept, so they never show as removed. |
| Slow fetch | the complete DEFRA catalogue | Each page of 200 datasets takes over a minute. Progress is saved after every page, so it resumes where it stopped. |

- Fast fetch reuses a copy younger than 24 hours instead of fetching again, and suggests a slow fetch
  once the last one is more than 30 days old (both adjustable in Settings; 0 switches them off).
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
| QGIS profile `BrowserFetcherUK/` | `applied.json` (last Apply), `owned.json` (connections the plugin added), `choices.json` (your ticks), `backups/` (last 10), `custom_connections.json`, `sources.json` |

## Issues

https://github.com/ZedeN1/BrowserFetcherUK/issues
