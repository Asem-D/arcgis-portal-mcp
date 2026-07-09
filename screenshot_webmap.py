"""Take a screenshot of a public ArcGIS Online webmap using Playwright."""
import asyncio
from playwright.async_api import async_playwright

WEBMAP_ID = "ed19b095c1474858b8ecd0b49b649012"
WEBMAP_URL = f"https://dargis.maps.arcgis.com/home/webmap/viewer.html?webmap={WEBMAP_ID}"
OUTPUT_FILE = "webmap_screenshot.png"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--isolated"],
            executable_path="C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        print(f"Navigating to {WEBMAP_URL}")
        await page.goto(WEBMAP_URL, wait_until="networkidle", timeout=60000)
        # Wait for map tiles to load - ArcGIS JS API needs more time
        await page.wait_for_timeout(15000)
        await page.screenshot(path=OUTPUT_FILE, full_page=False)
        print(f"Screenshot saved to {OUTPUT_FILE}")
        await browser.close()

asyncio.run(main())
