from __future__ import annotations

import random
from typing import Any, Dict, List


def _normalize_host(hostname: str | None) -> str:
    if not hostname:
        return ""
    hostname = hostname.lower()
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname


def build_link_set(
    slug: str, data: Dict[str, Any], desired: int = 6, current_host: str | None = None
) -> List[Dict[str, Any]]:
    pages = data.get("pages", {})
    externals = data.get("external_links", [])
    normalized_host = _normalize_host(current_host)
    domains = [_normalize_host(item.get("host")) for item in data.get("domains", [])]

    internal_candidates = [
        key
        for key, page in pages.items()
        if key != slug and _normalize_host(page.get("host")) != normalized_host
    ]
    fallback_candidates = [key for key in pages.keys() if key != slug and key not in internal_candidates]
    random.shuffle(internal_candidates)
    random.shuffle(fallback_candidates)

    cross_domain_hosts = [host for host in domains if host and host != normalized_host]
    random.shuffle(cross_domain_hosts)

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
        host_hint = page_host or (cross_domain_hosts[0] if cross_domain_hosts else "")
        cross_domain = host_hint and host_hint != normalized_host
        links.append(
            {
                "label": pages[target].get("title", target).split("|")[0],
                "url": f"//{host_hint}/p/{target}" if cross_domain else f"/p/{target}",
                "external": cross_domain,
            }
        )

    if not links and slug not in pages:
        links.append(
            {
                "label": "内容矩阵首页",
                "url": "/",
                "external": False,
            }
        )

    return links[:desired]
