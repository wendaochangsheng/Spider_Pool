from __future__ import annotations

import json
import os
import random
import re
import textwrap
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests

from .storage import record_ai_event

DEEPSEEK_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")


def _call_deepseek(prompt: str, model: str, *, max_tokens: int) -> Tuple[Dict[str, Any] | None, str | None]:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None, "缺少 DEEPSEEK_API_KEY 环境变量"

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
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(DEEPSEEK_URL, json=payload, headers=headers, timeout=45)
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:  # pragma: no cover - requests errors
        return None, str(exc)


def _normalize_json_text(content: str) -> str | None:
    cleaned = content.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = cleaned.split("```", 1)[0].strip()

    if "{" in cleaned and "}" in cleaned:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        cleaned = cleaned[start : end + 1]

    if not cleaned.startswith("{"):
        return None

    return cleaned


def _clean_text(html: str, limit: int = 1200) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:limit]


def _read_reference_sources(urls: List[str], limit_per: int = 1200) -> str | None:
    snippets: List[str] = []
    for url in urls[:5]:
        try:
            response = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0 (compatible; SpiderPoolBot/1.0)"},
            )
            response.raise_for_status()
            cleaned = _clean_text(response.text, limit_per)
            if cleaned:
                snippets.append(f"{url}: {cleaned}")
        except Exception as exc:  # pragma: no cover - network variability
            record_ai_event(
                "参考源抓取失败",
                level="warning",
                meta={"url": url, "error": str(exc)},
            )
    if not snippets:
        return None
    combined = "\n".join(snippets[:3])
    return combined[: limit_per * 3]


def _structured_payload(
    topic: str,
    keywords: List[str],
    host: str,
    links: List[Dict[str, Any]],
    *,
    min_words: int,
    max_words: int,
    reference_context: str | None,
) -> str:
    keyword_text = ", ".join(keywords) if keywords else "泛行业词"
    links_text = "\n".join(f"- {item['label']}: {item['url']}" for item in links)
    word_hint = f"全文控制在 {min_words}-{max_words} 字之间，保持自然中文表达。"
    reference_hint = (
        f"可参考以下摘录补充语料：\n{reference_context}"
        if reference_context
        else "不必强调数据源，保持内容自然顺滑。"
    )
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
          - 文风像行业资讯稿，避免夸张用语，{word_hint}
          - 结合站点 {host} 的语境，自然地描述主题与关键词的联系。
          - 若存在下列链接，请合理过渡后提及：\n{links_text or '无特定链接'}。
          - {reference_hint}
          - 若收到类似 “pool-1234” 或仅含数字的占位词，请改写成自然、正规、无数字堆砌的主题再输出。
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
        "topic": formal_topic,
    }


def _formalize_topic(raw_topic: str, keywords: List[str], host: str) -> str:
    topic = (raw_topic or "").strip()
    if not topic:
        topic = random.choice([
            "行业趋势速览",
            "热门话题精选",
            "应用体验解读",
            "产品动态聚合",
        ])

    lowered = topic.lower()
    if re.fullmatch(r"(pool|page)[\s_-]*\d{3,5}", lowered) or re.fullmatch(r"\d{3,6}", lowered):
        base = (keywords[0] if keywords else "站点") or host.split(":")[0]
        templates = [
            f"{base} 主题解读",
            f"{base} 体验速写",
            f"{base} 热点汇编",
            f"{base} 资讯脉络",
        ]
        topic = random.choice(templates)

    return topic


def _safe_title(title: str | None, topic: str) -> str:
    candidate = (title or "").strip()
    if not candidate:
        candidate = topic

    lowered = candidate.lower()
    if re.search(r"pool[\s_-]*\d{3,5}", lowered):
        replacements = [
            f"{topic} 深度解读",
            f"{topic} 主题速览",
            f"{topic} 资讯要点",
        ]
        candidate = random.choice(replacements)

    return candidate


def generate_article(
    topic: str,
    keywords: List[str],
    host: str,
    links: List[Dict[str, Any]],
    *,
    min_words: int | None = None,
    max_words: int | None = None,
    reference_urls: List[str] | None = None,
) -> Dict[str, str]:
    formal_topic = _formalize_topic(topic, keywords, host)
    min_words_env = int(os.environ.get("ARTICLE_MIN_WORDS", 0)) if os.environ.get("ARTICLE_MIN_WORDS") else None
    max_words_env = int(os.environ.get("ARTICLE_MAX_WORDS", 0)) if os.environ.get("ARTICLE_MAX_WORDS") else None
    min_words = max(min_words or min_words_env or 800, 200)
    max_words = max(max_words or max_words_env or min_words + 400, min_words + 200)
    max_tokens = max(800, min(max_words * 2, 3500))
    reference_urls = reference_urls or (
        [item.strip() for item in os.environ.get("REFERENCE_URLS", "").split(",") if item.strip()]
        if os.environ.get("REFERENCE_URLS")
        else []
    )
    reference_context = _read_reference_sources(reference_urls)
    prompt = _structured_payload(
        formal_topic,
        keywords,
        host,
        links,
        min_words=min_words,
        max_words=max_words,
        reference_context=reference_context,
    )
    print(f"[AI] 开始生成: topic='{topic}', keywords={keywords}, host='{host}'", flush=True)
    print(f"[AI] 提示词片段: {prompt[:280]}...", flush=True)
    try:
        response, error = _call_deepseek(
            prompt,
            os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            max_tokens=max_tokens,
        )
        if response:
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"[AI] 原始响应片段: {content[:320]}...", flush=True)
            try:
                normalized = _normalize_json_text(content)
                if not normalized:
                    raise json.JSONDecodeError("未找到可解析的 JSON 结构", content, 0)
                print(f"[AI] 解析内容片段: {normalized[:280]}...", flush=True)
                structured = json.loads(normalized)
                structured["title"] = _safe_title(structured.get("title"), formal_topic)
            except json.JSONDecodeError as exc:
                record_ai_event(
                    "DeepSeek 返回无法解析的内容",
                    level="error",
                    meta={"topic": topic, "keywords": keywords, "error": str(exc)},
                )
                print(f"[AI] JSON 解析失败: {exc}", flush=True)
                return _fallback_article(formal_topic, keywords, links)
            article = _build_html(structured, links)
            article["generator"] = "deepseek"
            article["model"] = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
            article["topic"] = formal_topic
            record_ai_event(
                "DeepSeek 生成成功",
                level="info",
                meta={
                    "topic": topic,
                    "keywords": keywords,
                    "model": article["model"],
                    "min_words": min_words,
                    "max_words": max_words,
                    "references": reference_urls,
                },
            )
            print("[AI] 生成完成，已写入内容。", flush=True)
            return article
        if error:
            record_ai_event(
                "DeepSeek 调用失败",
                level="error",
                meta={"topic": topic, "keywords": keywords, "error": error},
            )
            print(f"[AI] 调用错误: {error}", flush=True)
    except Exception as exc:  # pragma: no cover - defensive fallback
        record_ai_event(
            "DeepSeek 处理异常",
            level="error",
            meta={"topic": topic, "keywords": keywords, "error": str(exc)},
        )
        print(f"[AI] 异常: {exc}", flush=True)

    return _fallback_article(formal_topic, keywords, links)
