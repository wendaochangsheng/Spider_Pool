from __future__ import annotations

import json
import os
import random
import textwrap
from datetime import datetime
from typing import Any, Dict, List

import requests

DEEPSEEK_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")


def _call_deepseek(prompt: str, model: str) -> Dict[str, Any] | None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.environ.get("DEEPSEEK_MODEL", model),
        "messages": [
            {"role": "system", "content": "You are an SEO copywriter generating pseudo-original Chinese long-form articles."},
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.8,
        "max_tokens": 900,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=45)
    response.raise_for_status()
    return response.json()


def _structured_payload(topic: str, keywords: List[str], host: str, links: List[Dict[str, Any]]) -> str:
    keyword_text = ", ".join(keywords) if keywords else "泛行业词"
    links_text = "\n".join(f"- {item['label']}: {item['url']}" for item in links)
    return textwrap.dedent(
        f"""
        你是一名中文 SEO 文案，需要根据主题“{topic}”与关键词「{keyword_text}」生成完全原创、可读性高的长文。
        输出必须是合法 JSON 字符串，顶层字段：
          title (string): 正常中文标题，禁止出现“观察”“策略”等敏感描述。
          intro (string): 2-3 句自然引言，语气平实。
          sections (array): 至少 3 个对象，每个包含 heading(string) 与 content(array[string])，content 里给 2-3 个完整段落。
          bullets (array[string]): 3-5 个重点提示，避免重复。
          closing (string): 1 段收尾总结。
        写作要求：
          - 文风像行业资讯稿，避免夸张用语。
          - 结合站点 {host} 的语境，自然地描述主题与关键词的联系。
          - 若存在下列链接，请合理过渡后提及：\n{links_text or '无特定链接'}。
          - 禁止输出 markdown、HTML 或额外解释，只能返回 JSON。
        """
    ).strip()


def _build_html(payload: Dict[str, Any], links: List[Dict[str, Any]]) -> Dict[str, str]:
    title = payload.get("title") or payload.get("heading") or "无题文章"
    intro = payload.get("intro", "")
    sections = payload.get("sections", [])
    bullets = payload.get("bullets", [])
    closing = payload.get("closing", "")

    body_parts = [f"<p class=\"excerpt\">{intro}</p>"] if intro else []
    for section in sections:
        heading = section.get("heading") or "洞察"
        content = section.get("content")
        if isinstance(content, list):
            paragraphs = content
        else:
            paragraphs = [content] if content else []
        body_parts.append(f"<h2>{heading}</h2>")
        for paragraph in paragraphs:
            body_parts.append(f"<p>{paragraph}</p>")

    if bullets:
        body_parts.append("<div class=\"key-points\"><h3>要点快览</h3><ul>")
        for item in bullets:
            body_parts.append(f"<li>{item}</li>")
        body_parts.append("</ul></div>")

    if closing:
        body_parts.append(f"<p class=\"closing\">{closing}</p>")

    if links:
        body_parts.append("<div class=\"related-links\"><h3>相关链接</h3><ul>")
        for item in links:
            rel = " rel=\"nofollow noopener\"" if item.get("external") else ""
            body_parts.append(f"<li><a href='{item['url']}'{rel}>{item['label']}</a></li>")
        body_parts.append("</ul></div>")

    return {
        "title": title,
        "excerpt": intro,
        "body": "\n".join(body_parts),
        "generated_at": datetime.utcnow().isoformat(),
    }


def _fallback_article(topic: str, keywords: List[str], links: List[Dict[str, Any]]) -> Dict[str, str]:
    keyword_text = "、".join(keywords) if keywords else "行业趋势"
    intro = f"{topic} 相关节点持续被关注，围绕 {keyword_text} 的语义线索输出长文内容，模拟真实站点更新轨迹。"
    paragraphs = [
        intro,
        f"页面在基础信息之外，穿插来自 {keyword_text} 的扩展描述，保证段落长度与语气保持自然，读起来像日常更新的资讯稿。",
        "内容中适度加入时间、场景、痛点等描述，制造动态记录感，让访客以为这是持续维护的专题站而非单页投放。",
        "在段落内部加入隐性引用与跳转提示，配合互链策略即可将主题拆分成多个子话题，形成网状内容结构。",
        "整体语调保持克制，不刻意渲染技术细节，而是像行业观察文章一样，淡化运营痕迹、突出洞察。",
    ]

    body_parts = [f"<p class=\"excerpt\">{paragraphs[0]}</p>"]
    for paragraph in paragraphs[1:]:
        body_parts.append(f"<p>{paragraph}</p>")

    if links:
        body_parts.append("<div class=\"related-links\"><h3>相关链接</h3><ul>")
        for item in links:
            rel = " rel=\"nofollow noopener\"" if item.get("external") else ""
            body_parts.append(f"<li><a href='{item['url']}'{rel}>{item['label']}</a></li>")
        body_parts.append("</ul></div>")

    formal_topic = topic.strip() or "内容矩阵"
    return {
        "title": f"{formal_topic} 主题长文",
        "excerpt": intro,
        "body": "\n".join(body_parts),
        "generated_at": datetime.utcnow().isoformat(),
        "generator": "local",
        "model": "template",
    }


def generate_article(topic: str, keywords: List[str], host: str, links: List[Dict[str, Any]]) -> Dict[str, str]:
    prompt = _structured_payload(topic, keywords, host, links)
    try:
        response = _call_deepseek(prompt, os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
        if response:
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            structured = json.loads(content)
            article = _build_html(structured, links)
            article["generator"] = "deepseek"
            article["model"] = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
            return article
    except Exception:
        # swallow error and fallback below
        pass

    return _fallback_article(topic, keywords, links)
