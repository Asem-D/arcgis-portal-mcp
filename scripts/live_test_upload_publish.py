"""Live test for upload_item + publish_from_item fixes.

Exercises the full pipeline against the real portal (AGOL or Enterprise):
  1. Connect via username/password (generateToken path)
  2. Upload a small CSV
  3. Publish it as a hosted feature service
  4. Query the resulting FeatureServer to verify features
  5. Clean up (delete test items)

Verified working against AGOL (dargis.maps.arcgis.com) on 2026-10-01.
Notes:
  - AGOL publish is async; the service URL returns immediately but the
    hosted FeatureServer takes ~10-15 seconds to provision. The script
    polls the layer endpoint until it responds.
  - Private hosted services require the token on query.
  - AGOL returns 'serviceurl' (lowercase) in the publish response.
"""

import csv
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

# Load .env from repo root
repo_root = Path(__file__).parent.parent
env_path = repo_root / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from arcgis_portal_mcp.client import ArcGISClient

PORTAL = os.environ.get("portal_url", "")
USERNAME = os.environ.get("username", "")
PASSWORD = os.environ.get("password", "")

if not all([PORTAL, USERNAME, PASSWORD]):
    print("SKIP: portal_url/username/password not set in .env")
    sys.exit(0)

print(f"Portal: {PORTAL}")
print(f"User:   {USERNAME}")

client = ArcGISClient()

# ── Step 1: Connect (generateToken path) ──────────────────────────
print("\n[1] Connecting via generateToken ...")
try:
    client.connect_username_password(PORTAL, USERNAME, PASSWORD)
    print(f"    OK. auth_method={client._auth_method}")
    print(f"    user GUID: {client._user_info.get('id', '(none)')}")
except Exception as e:
    print(f"    CONNECT FAILED: {e}")
    sys.exit(1)

token = client.token

# ── Step 2: Create a test CSV ─────────────────────────────────────
print("\n[2] Creating test CSV ...")
# Unique name per run to avoid 409 conflicts from prior test items
stamp = time.strftime("%Y%m%d_%H%M%S")
csv_name = f"nanoDell_test_points_{stamp}.csv"
csv_path = os.path.join(tempfile.gettempdir(), csv_name)
with open(csv_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["name", "lat", "lon", "value"])
    w.writerow(["Point A", "33.8886", "35.4955", "10"])
    w.writerow(["Point B", "33.8900", "35.5000", "20"])
    w.writerow(["Point C", "33.8850", "35.4900", "30"])
print(f"    Written: {csv_path}")

# ── Step 3: Upload ────────────────────────────────────────────────
print("\n[3] Uploading CSV via addItem ...")
upload_result = client.upload_file(
    csv_path,
    title=f"nanoDell Test Points {stamp}",
    type_="CSV",
    tags="test,nanodell",
    description="Temporary test upload for endpoint verification",
    access="private",
)
if "error" in upload_result:
    print(f"    UPLOAD FAILED: {upload_result}")
    sys.exit(1)
item_id = upload_result.get("id") or upload_result.get("item")
print(f"    OK. Item ID: {item_id}")

# ── Step 4: Publish ───────────────────────────────────────────────
print("\n[4] Publishing as hosted feature service ...")
# layerInfo is auto-generated from the CSV content when omitted
pub_params = {
    "name": f"nanoDellTestPoints_{stamp}",
    "locationType": "coordinates",
    "latitudeFieldName": "lat",
    "longitudeFieldName": "lon",
}
publish_result = client.publish_from_item(
    item_id,
    service_type="csv",
    publish_parameters=pub_params,
)
if "error" in publish_result:
    print(f"    PUBLISH FAILED: {publish_result}")
    sys.exit(1)

services = publish_result.get("services", [])
if not services or not services[0].get("serviceItemId"):
    print(f"    PUBLISH FAILED (no service created): {publish_result}")
    sys.exit(1)

# AGOL returns 'serviceurl' (lowercase); Enterprise uses 'serviceUrl'
service_url = services[0].get("serviceurl") or services[0].get("serviceUrl") or ""
service_item = services[0].get("serviceItemId", "")
print(f"    OK. Service URL: {service_url}")
print(f"    Service item:   {service_item}")

# ── Step 5: Query the FeatureServer (poll; publish is async) ──────
print("\n[5] Querying FeatureServer (polling; publish is async) ...")
query_url = f"{service_url}/0/query"
features = None
for attempt in range(12):
    resp = requests.get(
        query_url,
        params={"where": "1=1", "outFields": "*", "f": "json", "token": token},
        timeout=30,
    )
    data = resp.json()
    if data.get("error") and "Invalid URL" in str(data["error"]):
        print(f"    attempt {attempt + 1}: service not provisioned yet, waiting 5s ...")
        time.sleep(5)
        continue
    features = data.get("features", [])
    break

if features is None:
    print("    QUERY FAILED: service never provisioned")
    sys.exit(1)

print(f"    HTTP {resp.status_code} | features: {len(features)}")
for ft in features:
    print(f"      {ft.get('attributes')}")

if len(features) != 3:
    print(f"    UNEXPECTED: expected 3 features, got {len(features)}")
    sys.exit(1)

# ── Step 6: Cleanup ───────────────────────────────────────────────
print("\n[6] Cleanup ...")
for iid, label in [(item_id, "CSV source"), (service_item, "Feature service")]:
    if not iid:
        continue
    result = client.delete_item(iid)
    ok = result.get("success") if isinstance(result, dict) else result
    print(f"    delete {label}: {'OK' if ok else result}")

print("\nALL CHECKS PASSED")
