from __future__ import annotations
"""Vendor research: DB-first, Firecrawl fallback for unknown/internet vendors."""
import os
import re
import httpx

FIRECRAWL_URL = "https://api.firecrawl.dev/v2"


def _headers():
    return {"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}", "Content-Type": "application/json"}


# ── DB-first lookup ──────────────────────────────────────────────────────────

def resolve_vendor(vendor_name: str, product: str, quantity: str) -> dict:
    """
    Check vendor DB first. If found, return their details.
    If not found, use Firecrawl to search the web.
    """
    import db
    known = db.get_known_vendor(vendor_name)
    if known:
        return {
            "vendor_name": known["name"],
            "phone": known["phone"],
            "website": known["website"],
            "listed_price": None,
            "source": "db",
            "can_handle_quantity": (known["min_order"] <= int(quantity or 0) <= known["max_order"])
                                   if quantity and str(quantity).isdigit() else True,
        }
    # Not in DB — search web
    return search_vendor_online(vendor_name)


def resolve_all_vendors(vendor_names: list[str], product: str, quantity: str,
                        confirmed_set: set = None) -> list[dict]:
    confirmed_set = confirmed_set or set()
    results = []
    for name in vendor_names:
        try:
            if name.lower() in confirmed_set:
                import db as _db
                known = _db.get_known_vendor(name)
                if known:
                    info = {
                        "vendor_name": known["name"],
                        "phone": known["phone"],
                        "website": known["website"],
                        "listed_price": None,
                        "source": "db_confirmed",
                        "can_handle_quantity": (
                            known["min_order"] <= int(quantity or 0) <= known["max_order"]
                        ) if quantity and str(quantity).isdigit() else True,
                    }
                else:
                    info = search_vendor_online(name)
            else:
                info = resolve_vendor(name, product, quantity)
        except Exception as e:
            info = {"vendor_name": name, "phone": None, "website": None,
                    "listed_price": None, "source": "db", "error": str(e)}
        results.append(info)
    return results


# ── Firecrawl web search ─────────────────────────────────────────────────────

def search_vendor_online(vendor_name: str) -> dict:
    """Search + scrape to find phone number and pricing for an unknown vendor."""
    result = _search(f"{vendor_name} official website contact phone pricing")
    if not result.get("url"):
        return {"vendor_name": vendor_name, "phone": None, "website": None,
                "listed_price": None, "source": "web"}

    url = result["url"]
    content = _scrape(url)

    phone = _extract_phone(content)
    if not phone:
        contact_result = _search(f"{vendor_name} contact us phone number")
        if contact_result.get("url") and contact_result["url"] != url:
            contact_content = _scrape(contact_result["url"])
            phone = _extract_phone(contact_content)

    return {
        "vendor_name": vendor_name,
        "phone": phone,
        "website": url,
        "listed_price": _extract_price(content),
        "source": "web",
    }


def find_alternative_vendors(product: str, quantity: str, exclude_names: list[str] = None) -> list[dict]:
    """Search internet for vendors that supply a given product at a given quantity."""
    query = f"vendors suppliers {product} bulk {quantity} units contact phone pricing"
    r = httpx.post(
        f"{FIRECRAWL_URL}/search",
        headers=_headers(),
        json={"query": query, "limit": 5},
        timeout=30,
    )
    r.raise_for_status()
    web_results = r.json().get("data", {}).get("web", [])

    exclude = {n.lower() for n in (exclude_names or [])}
    vendors = []
    for item in web_results:
        url = item.get("url", "")
        title = item.get("title", "")
        # Skip excluded vendors
        if any(ex in title.lower() for ex in exclude):
            continue
        content = _scrape(url)
        phone = _extract_phone(content)
        vendors.append({
            "vendor_name": title.split("|")[0].split("-")[0].strip()[:60],
            "phone": phone,
            "website": url,
            "listed_price": _extract_price(content),
            "source": "web_alt",
        })
    return vendors


# ── Firecrawl helpers ────────────────────────────────────────────────────────

def _search(query: str) -> dict:
    r = httpx.post(
        f"{FIRECRAWL_URL}/search",
        headers=_headers(),
        json={"query": query, "limit": 3},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("data", {}).get("web", [])
    if not results:
        return {}
    return {"url": results[0].get("url", ""), "title": results[0].get("title", "")}


def _scrape(url: str) -> str:
    try:
        r = httpx.post(
            f"{FIRECRAWL_URL}/scrape",
            headers=_headers(),
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": 20000},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("markdown", "")[:3000]
    except Exception:
        return ""


def _extract_phone(text: str) -> str | None:
    for pattern in [
        r"\+1[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}",
        r"\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}",
        r"\+\d{1,3}[\s\-]?\d{6,12}",
    ]:
        m = re.search(pattern, text)
        if m:
            return m.group(0).strip()
    return None


def _extract_price(text: str) -> str | None:
    m = re.search(r"\$[\d,]+(?:\.\d{2})?(?:\s*(?:per|/)\s*(?:unit|piece|item|ea))?", text, re.IGNORECASE)
    return m.group(0) if m else None
