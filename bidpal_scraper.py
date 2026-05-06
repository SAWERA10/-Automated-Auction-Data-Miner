from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd
from playwright.sync_api import sync_playwright

START_URL = "https://one.bidpal.net/benefit26/browse/all"
OUTPUT_FILE = "benefit26_items.xlsx"

HEADLESS = False   # IMPORTANT: must be False to avoid 403
DEBUG_MODE = True


# ---------------- UTILITIES ----------------

def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def block_assets(route):
    if route.request.resource_type in {"image", "media", "font"}:
        return route.abort()
    return route.continue_()


def normalize_response_to_json(data: Any) -> Any:
    if isinstance(data, dict):
        for key in ["items", "data", "result", "response", "payload"]:
            if key in data and isinstance(data[key], (dict, list)):
                return data[key]
    return data


def walk(obj: Any) -> List[Dict[str, Any]]:
    found = []

    if isinstance(obj, dict):
        keys = {str(k).lower() for k in obj.keys()}
        if len(keys.intersection({"title", "name", "id", "itemid"})) >= 2:
            found.append(obj)
        for v in obj.values():
            found.extend(walk(v))

    elif isinstance(obj, list):
        for v in obj:
            found.extend(walk(v))

    return found


def primitive_text(value: Any) -> str:
    if isinstance(value, (str, int, float)):
        return norm(value)

    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (str, int, float)):
                return norm(v)
        for v in value.values():
            txt = primitive_text(v)
            if txt:
                return txt

    if isinstance(value, list):
        for v in value:
            txt = primitive_text(v)
            if txt:
                return txt

    return ""


def flatten_pairs(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    out = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            out.append((path, v))
            out.extend(flatten_pairs(v, path))

    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            path = f"{prefix}[{i}]"
            out.append((path, v))
            out.extend(flatten_pairs(v, path))

    return out


def match_any(path: str, patterns: Iterable[str]) -> bool:
    p = path.lower()
    return any(re.search(x, p) for x in patterns)


def find_value(obj: Any, patterns: Iterable[str]) -> str:
    for path, value in flatten_pairs(obj):
        if match_any(path, patterns):
            txt = primitive_text(value)
            if txt:
                return txt
    return ""


def donor_from_name(name: str) -> str:
    name = norm(name)
    if ":" in name:
        return name.split(":")[0].replace("-", "").strip()
    return ""


def build_row(d: Dict[str, Any]) -> Dict[str, str]:

    title = find_value(d, [r"title", r"name", r"headline"])

    donor = find_value(d, [
        r"donor", r"sponsor", r"vendor", r"company", r"organization"
    ])

    value = find_value(d, [
        r"value", r"price", r"bid", r"estimate", r"amount"
    ])

    desc = find_value(d, [r"description", r"details", r"summary"])
    item_id = find_value(d, [r"itemid", r"id", r"uuid"])

    if not donor:
        donor = donor_from_name(title)

    link = f"https://one.bidpal.net/benefit26/details:item/{item_id}" if item_id else ""

    return {
        "Item Headline": title,
        "Donated By": donor,
        "Value": value,
        "Direct Link": link,
        "Description": desc,
        "_raw": d
    }


# ---------------- MAIN SCRAPER ----------------

def main():
    print("Opening browser...")

    captured_json = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        )

        context.route("**/*", block_assets)

        page = context.new_page()

        # CAPTURE API RESPONSE FROM NETWORK
        def handle_response(response):
            nonlocal captured_json
            if "getAllItems" in response.url and response.status == 200:
                try:
                    captured_json = response.json()
                    print("✅ API captured from network")
                except:
                    pass

        page.on("response", handle_response)

        # OPEN PAGE
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)

        # WAIT FOR API
        print("Waiting for API response...")

        for _ in range(60):  # ~30 sec wait
            if captured_json:
                break
            page.wait_for_timeout(500)

        browser.close()

    if not captured_json:
        raise RuntimeError("API not captured. Try running again with headless=False.")

    data = normalize_response_to_json(captured_json)
    items = walk(data)

    rows = []

    for d in items:
        row = build_row(d)
        row.pop("_raw", None)
        rows.append(row)

    df = pd.DataFrame(rows).drop_duplicates()
    df.to_excel(OUTPUT_FILE, index=False)

    print("\n✅ DONE")
    print("Items scraped:", len(df))
    print("Saved file:", OUTPUT_FILE)


if __name__ == "__main__":
    main() 