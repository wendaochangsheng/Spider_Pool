from __future__ import annotations

import random
import string
from typing import Any, Dict, List


def _normalize_host(hostname: str | None) -> str:
    if not hostname:
        return ""
    hostname = hostname.lower()
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname


def _random_subdomain(base_host: str) -> str:
    if not base_host:
        return ""

    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(3, 8)))
    return f"{prefix}.{base_host}"


def build_link_set(
    slug: str, data: Dict[str, Any], desired: int = 6, current_host: str | None = None
) -> List[Dict[str, Any]]:
    pages = data.get("pages", {})
    externals = data.get("external_links", [])
    normalized_host = _normalize_host(current_host)
    domains = [_normalize_host(item.get("host")) for item in data.get("domains", [])]
    preferred_base = normalized_host or next((host for host in domains if host), "")
    anchor_base = preferred_base or (domains[0] if domains else "")

    internal_candidates = [
        key
        for key, page in pages.items()
        if key != slug and _normalize_host(page.get("host")) != normalized_host
    ]
    fallback_candidates = [key for key in pages.keys() if key != slug and key not in internal_candidates]
    random.shuffle(internal_candidates)
    random.shuffle(fallback_candidates)

    links: List[Dict[str, Any]] = []

    if externals:
        pool = externals * ((desired // len(externals)) + 1)
        random.shuffle(pool)
        for item in pool[: desired // 2 or desired]:
            links.append(
                {
                    "label": item.get("label") or item.get("url"),
                    "url": item.get("url"),
                    "external": True,
                }
            )

    while len(links) < desired and (internal_candidates or fallback_candidates):
        target = internal_candidates.pop() if internal_candidates else fallback_candidates.pop()
        page_host = _normalize_host(pages.get(target, {}).get("host"))
        base_host = anchor_base or page_host or preferred_base or (domains[0] if domains else "")
        host_hint = _random_subdomain(base_host) if base_host else ""
        cross_domain = bool(host_hint)
        links.append(
            {
                "label": pages[target].get("title", target).split("|")[0],
                "url": f"//{host_hint}/p/{target}" if cross_domain else f"/p/{target}",
                "external": cross_domain,
            }
        )

    if not links and slug not in pages:
        base_host = anchor_base or preferred_base or (domains[0] if domains else "")
        host_hint = _random_subdomain(base_host) if base_host else ""
        cross_domain = bool(host_hint)
        links.append(
            {
                "label": "内容矩阵首页",
                "url": f"//{host_hint}/" if cross_domain else "/",
                "external": cross_domain,
            }
        )

    return links[:desired]
