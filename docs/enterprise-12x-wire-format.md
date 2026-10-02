# ArcGIS Enterprise 12.x Wire-Format Notes

Live-verified against an Enterprise 12.0.0 portal (Portal 2025.2, Linux, Build 58905).
These are the non-obvious wire-format behaviors that cost debugging time. If a
publish/upload/admin operation silently fails on Enterprise 12.x, check this list first.

## Content Upload and Publish

| Behavior | Detail |
|----------|--------|
| Upload endpoint | `/content/users/{owner}/addItem` — the `/add` endpoint does not exist and returns an error |
| Publish URL owner | Must use the user's **GUID** (from `/community/self`, field `id`), not the username. The username path returns `{"success": true}` but the hosted service is never created on ArcGIS Server |
| Publish `fileType` | CamelCase parameter name, lowercase value: `fileType=csv`, `fileType=shapefile`, `fileType=geojson` |
| Upload item type | `type="GeoJson"` with capital J. Lowercase `geojson` fails with error `CONT_0113` |
| CSV publish | ArcGIS silently fails without a `layerInfo` defining the fields. Auto-generate from headers + first data row; detect lat/lon fields by header name (value-based inference misclassifies coordinate fields as plain doubles) |

## Geometry Service

| Behavior | Detail |
|----------|--------|
| Buffer request | `geometries` must be a `"x,y"` coordinate string (not a JSON array), and the `bufferSR` parameter is required. Omitting `bufferSR` fails even when `inSR` is set |

## Server Administration (v1.15.0)

| Behavior | Detail |
|----------|--------|
| Admin URL derivation | `/portals/self` no longer exposes a `servicesServer` field on 12.x. The hosting server admin base is derived from `helperServices.geometry.url`: split on `/rest/services/` and append `/admin` (e.g. `https://host/arcgis/rest/services/Geometry/GeometryServer` → `https://host/arcgis/admin`) |
| Service admin endpoints | Public service URLs convert to admin by replacing `/rest/services/` with `/admin/services/` (e.g. `.../rest/services/Folder/Name.FeatureServer` → `.../admin/services/Folder/Name.FeatureServer`) |
| Stopped services | Querying admin details of a stopped service returns `{"status": "error"}` from the server. This is correct ArcGIS Server behavior, not a client bug |
| portaladmin location | The Portal Admin API lives under the portal URL (`{portal}/portaladmin`), not under the server URL. Server admin lives under the derived hosting server admin base |

## Tokens

| Behavior | Detail |
|----------|--------|
| Token lifetime | Portal issues 30-minute tokens for `authorization_code` grants regardless of the client's assumed default. Raise the token lifetime in Portal admin Security settings for long sessions |