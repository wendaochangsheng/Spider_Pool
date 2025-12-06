from __future__ import annotations

import random
from typing import Any, Dict, List


def build_link_set(slug: str, data: Dict[str, Any], desired: int = 6) -> List[Dict[str, Any]]:
    pages = data.get("pages", {})
    externals = data.get("external_links", [])

    internal_candidates = [key for key in pages.keys() if key != slug]
    random.shuffle(internal_candidates)

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

    while len(links) < desired and internal_candidates:
        target = internal_candidates.pop()
        links.append(
            {
                "label": pages[target].get("title", target).split("|")[0],
                "url": f"/p/{target}",
                "external": False,
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
