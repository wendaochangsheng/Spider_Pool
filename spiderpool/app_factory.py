from __future__ import annotations

import os
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import List

from flask import (
    Flask,
    flash,
    Response,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    stream_with_context,
    session,
    url_for,
)

from .content import generate_article, request_ai_theme
from .links import build_link_set
from .storage import load_data, save_data, update_data, record_view

ADMIN_USERNAME = os.environ.get("SPIDERPOOL_ADMIN", "admin")
ADMIN_PASSWORD = os.environ.get("SPIDERPOOL_PASSWORD", "admin")

SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
PAGE_LOCK = Lock()
TOPIC_PREFIX = ["趋势", "洞察", "应用", "热点", "动态", "观察", "案例", "体验"]
TOPIC_SUFFIX = ["精选", "速览", "解读", "全景", "拆解", "图谱", "要点", "集锦"]


def _is_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in {"1", "true", "on", "yes", "y"}


def slugify(text: str) -> str:
    slug = SLUG_PATTERN.sub("-", text.lower()).strip("-")
    return slug or f"page-{random.randint(1000, 9999)}"


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _random_theme(settings: dict, domains: list, host: str) -> tuple[str, list[str], str]:
    log_to_terminal = bool(settings.get("ai_console_log", True))
    ai_topic, ai_keywords = request_ai_theme(
        host,
        model=settings.get("deepseek_model", "deepseek-chat"),
        log_to_terminal=log_to_terminal,
    )
    if ai_topic and ai_keywords:
        slug = slugify(f"{ai_keywords[0]}-{random.randint(1000, 9999)}")
        return ai_topic, ai_keywords, slug

    domain_topics = [item.get("topic") for item in domains if item.get("topic")]
    keyword_pool = [kw for kw in settings.get("default_keywords", []) if kw]
    base_seed = random.choice(domain_topics + keyword_pool + ["行业", "产品", "体验", "趋势", "方案"])
    topic = f"{random.choice(TOPIC_PREFIX)}{base_seed}{random.choice(TOPIC_SUFFIX)}"
    keywords = keyword_pool[:]
    random.shuffle(keywords)
    if not keywords:
        keywords = [base_seed]
    keywords = keywords[:3] or [base_seed]
    slug = slugify(f"{base_seed}-{random.randint(1000, 9999)}")
    return topic, keywords, slug


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )
    app.secret_key = os.environ.get("SPIDERPOOL_SECRET", os.urandom(24))

    @app.context_processor
    def inject_globals():
        data = load_data()
        return {
            "pool_settings": data.get("settings", {}),
            "domain_list": data.get("domains", []),
        }

    def _is_authenticated() -> bool:
        return session.get("admin_logged_in", False)

    def _require_authentication():
        if not _is_authenticated():
            return redirect(url_for("admin_login"))
        return None

    def _domain_overview(pages: dict, stats: dict) -> list[dict]:
        overview: dict[str, dict] = {}
        for slug, page in pages.items():
            host = page.get("host") or "未记录"
            entry = overview.setdefault(host, {"count": 0, "views": 0, "latest": None})
            entry["count"] += 1
            entry["views"] += int(stats.get(slug, 0))
            timestamp = page.get("updated_at")
            if timestamp and (entry["latest"] is None or timestamp > entry["latest"]):
                entry["latest"] = timestamp
        return [
            {"host": host, **metrics}
            for host, metrics in sorted(overview.items(), key=lambda item: item[1]["views"], reverse=True)
        ]

    def _admin_payload():
        data = load_data()
        pages = data.get("pages", {})
        stats_map = data.get("view_stats", {})
        sorted_stats = sorted(stats_map.items(), key=lambda item: item[1], reverse=True)
        bot_hits = data.get("bot_hits", [])
        domain_stats = _domain_overview(pages, stats_map)
        return data, pages, stats_map, sorted_stats, bot_hits, domain_stats

    def _register_host(hostname: str) -> None:
        if not hostname:
            return

        def _mutate(payload):
            domains = payload.setdefault("domains", [])
            if not any(item.get("host") == hostname for item in domains):
                domains.append({"host": hostname, "label": hostname, "topic": ""})

        update_data(_mutate)

    def _filter_pages_by_host(host: str, data: dict) -> list[dict]:
        pages = list(data.get("pages", {}).values())
        host_pages = [page for page in pages if page.get("host") == host]
        return host_pages or pages

    def _resolve_random_page(host: str, path_hint: str | None = None) -> dict:
        data = load_data()
        pages = _filter_pages_by_host(host, data)
        slug_candidate = slugify(path_hint or "") if path_hint else ""
        if slug_candidate and slug_candidate in data.get("pages", {}):
            page_slug = slug_candidate
        elif pages:
            page_slug = random.choice(pages).get("slug")
        else:
            page_slug = slug_candidate or slugify(f"entry-{random.randint(1000, 9999)}")
        return _ensure_page(page_slug, host=host)

    def _ensure_page(
        slug: str,
        *,
        topic: str | None = None,
        keywords: List[str] | None = None,
        host: str | None = None,
        min_words: int | None = None,
        max_words: int | None = None,
        references: List[str] | None = None,
        force: bool = False,
    ):
        with PAGE_LOCK:
            data = load_data()
            page = data.get("pages", {}).get(slug) or {"slug": slug}
            links = build_link_set(slug, data)
            settings = data.get("settings", {})
            log_to_terminal = bool(settings.get("ai_console_log", True))

            if topic:
                page["topic"] = topic
            if keywords is not None:
                if isinstance(keywords, str):
                    keywords_list = [item.strip() for item in keywords.split(",") if item.strip()]
                else:
                    keywords_list = keywords
                page["keywords"] = keywords_list

            needs_generation = force or not page.get("body")
            keyword_seed = page.get("keywords") or settings.get("default_keywords", [])
            if isinstance(keyword_seed, str):
                keyword_seed = [item.strip() for item in keyword_seed.split(",") if item.strip()]
            topic_seed = page.get("topic") or slug.replace("-", " ")
            host_ref = host or page.get("host") or "pool.local"
            article_min = max(int(min_words or settings.get("article_min_words", 800) or 800), 200)
            article_max = max(int(max_words or settings.get("article_max_words", article_min + 400) or article_min + 400), article_min + 200)
            reference_urls = references or []

            if not needs_generation:
                if host and host != page.get("host"):
                    page["host"] = host
                    page["updated_at"] = datetime.utcnow().isoformat()
                    data.setdefault("pages", {})[slug] = page
                    save_data(data)
                if page.get("links") != links:
                    page["links"] = links
                    data.setdefault("pages", {})[slug] = page
                    save_data(data)
                return page

        article = generate_article(
            topic_seed,
            keyword_seed,
            host_ref,
            links,
            min_words=article_min,
            max_words=article_max,
            reference_urls=reference_urls,
            log_to_terminal=log_to_terminal,
        )

        with PAGE_LOCK:
            data = load_data()
            page = data.get("pages", {}).get(slug) or {"slug": slug}
            page.update(article)
            page["links"] = links
            page["host"] = host_ref
            page["keywords"] = keyword_seed
            page["topic"] = article.get("topic", topic_seed)
            page["updated_at"] = datetime.utcnow().isoformat()

            data.setdefault("pages", {})[slug] = page
            save_data(data)
            return page

    @app.route("/")
    def landing():
        host = request.host.split(":")[0]
        _register_host(host)
        data = load_data()
        pages = _filter_pages_by_host(host, data)
        random.shuffle(pages)
        spotlight = pages[:8]
        def _sort_by_updated(page):
            try:
                return datetime.fromisoformat(page.get("updated_at"))
            except Exception:
                return datetime.min
        latest_pages = sorted(pages, key=_sort_by_updated, reverse=True)[:6]
        stats_map = data.get("view_stats", {})
        hottest = sorted(stats_map.items(), key=lambda item: item[1], reverse=True)[:6]
        stats = data.get("view_stats", {})
        slug_lookup = {page.get("slug"): page for page in pages}
        leaderboard = []
        for slug, count in stats.items():
            page = slug_lookup.get(slug, {})
            label = page.get("topic") or page.get("title") or slug
            leaderboard.append({"slug": slug, "count": count, "label": label})
        leaderboard = sorted(leaderboard, key=lambda item: item["count"], reverse=True)
        shuffled_links = data.get("external_links", [])[:]
        random.shuffle(shuffled_links)
        return render_template(
            "index.html",
            pages=spotlight,
            page_total=len(pages),
            stats=stats,
            leaderboard=leaderboard,
            host=host,
            external_links=shuffled_links[:8],
            latest_pages=latest_pages,
            hottest=hottest,
        )

    @app.route("/p/<slug>")
    def show_page(slug: str):
        host = request.host.split(":")[0]
        _register_host(host)
        page = _ensure_page(slug, host=host)
        record_view(slug, user_agent=request.headers.get("User-Agent"))
        return render_template("page.html", page=page, host=host, dynamic_links=page.get("links", []))

    @app.errorhandler(404)
    def fallback_page(error):  # noqa: ANN001
        host = request.host.split(":")[0]
        _register_host(host)
        page = _resolve_random_page(host)
        record_view(page.get("slug"), user_agent=request.headers.get("User-Agent"))
        return render_template("page.html", page=page, host=host, dynamic_links=page.get("links", []))

    @app.route("/<path:any_path>")
    def wildcard_page(any_path: str):
        reserved_prefixes = ("admin", "api", "static")
        if any_path.startswith(reserved_prefixes) or any_path in {"robots.txt", "favicon.ico"}:
            return make_response("未找到内容", 404)

        host = request.host.split(":")[0]
        _register_host(host)
        slug_hint = any_path.rsplit("/", 1)[-1]
        page = _resolve_random_page(host, slug_hint)
        record_view(page.get("slug"), user_agent=request.headers.get("User-Agent"))
        return render_template("page.html", page=page, host=host, dynamic_links=page.get("links", []))

    @app.route("/robots.txt")
    def robots():
        robots_body = "User-agent: *\nDisallow: /\n"
        response = make_response(robots_body)
        response.headers["Content-Type"] = "text/plain"
        return response

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session["admin_logged_in"] = True
                flash("登录成功", "success")
                return redirect(url_for("admin_dashboard"))
            flash("账户或密码错误", "danger")
        return render_template("admin/login.html")

    @app.route("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.route("/admin")
    def admin_dashboard():
        guard = _require_authentication()
        if guard:
            return guard
        data, pages, stats_map, sorted_stats, bot_hits, domain_stats = _admin_payload()
        return render_template(
            "admin/dashboard.html",
            pages=pages,
            stats=sorted_stats,
            stats_map=stats_map,
            bot_hits=bot_hits,
            domain_stats=domain_stats,
            settings=data.get("settings", {}),
            ai_logs=data.get("ai_logs", []),
            active="overview",
        )

    @app.route("/admin/content")
    def admin_content():
        guard = _require_authentication()
        if guard:
            return guard
        data, pages, stats_map, sorted_stats, _, domain_stats = _admin_payload()
        try:
            current_page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            current_page = 1
        per_page = 20
        page_items = sorted(
            pages.values(),
            key=lambda item: item.get("updated_at") or item.get("generated_at") or "",
            reverse=True,
        )
        total_pages = max(1, (len(page_items) + per_page - 1) // per_page)
        start = (current_page - 1) * per_page
        page_slice = page_items[start : start + per_page]
        return render_template(
            "admin/content.html",
            pages=pages,
            page_items=page_slice,
            stats=sorted_stats,
            stats_map=stats_map,
            domain_stats=domain_stats,
            settings=data.get("settings", {}),
            pagination={
                "current": current_page,
                "total": total_pages,
                "per_page": per_page,
                "count": len(page_items),
            },
            active="content",
        )

    @app.route("/admin/settings")
    def admin_settings_page():
        guard = _require_authentication()
        if guard:
            return guard
        data, _, _, _, bot_hits, domain_stats = _admin_payload()
        return render_template(
            "admin/settings.html",
            domains=data.get("domains", []),
            external_links=data.get("external_links", []),
            settings=data.get("settings", {}),
            bot_hits=bot_hits,
            domain_stats=domain_stats,
            active="settings",
        )

    @app.route("/admin/domains", methods=["POST"])
    def admin_domains():
        guard = _require_authentication()
        if guard:
            return guard
        host = request.form.get("host", "").strip().lower()
        label = request.form.get("label", "").strip() or host
        topic = request.form.get("topic", "").strip()
        if host:
            def _mutate(payload):
                domains = payload.setdefault("domains", [])
                existing = next((item for item in domains if item.get("host") == host), None)
                if existing:
                    existing.update({"label": label, "topic": topic})
                else:
                    domains.append({"host": host, "label": label, "topic": topic})

            update_data(_mutate)
            flash("域名配置已更新", "success")
        return redirect(url_for("admin_settings_page"))

    @app.route("/admin/domains/delete", methods=["POST"])
    def admin_domains_delete():
        guard = _require_authentication()
        if guard:
            return guard
        host = request.form.get("host")
        if host:
            def _mutate(payload):
                payload["domains"] = [item for item in payload.get("domains", []) if item.get("host") != host]

            update_data(_mutate)
            flash("域名已移除", "info")
        return redirect(url_for("admin_settings_page"))

    @app.route("/admin/external-links", methods=["POST"])
    def admin_external_links():
        guard = _require_authentication()
        if guard:
            return guard
        action = request.form.get("action", "add")
        if action == "delete":
            url = request.form.get("url")
            if url:
                def _mutate(payload):
                    payload["external_links"] = [item for item in payload.get("external_links", []) if item.get("url") != url]

                update_data(_mutate)
                flash("外链已移除", "info")
        else:
            label = request.form.get("label", "").strip()
            url = request.form.get("url", "").strip()
            if url:
                def _mutate(payload):
                    links = payload.setdefault("external_links", [])
                    links.append({"label": label or url, "url": url})

                update_data(_mutate)
                flash("外链已加入池内", "success")
        return redirect(url_for("admin_settings_page"))

    @app.route("/admin/pages", methods=["POST"])
    def admin_pages():
        guard = _require_authentication()
        if guard:
            return guard
        topic = request.form.get("topic", "主题跟进")
        keywords = request.form.get("keywords", "")
        slug = request.form.get("slug")
        slug = slugify(slug or topic)
        reference_urls = [item.strip() for item in request.form.get("reference_urls", "").split(",") if item.strip()]
        try:
            min_words = int(request.form.get("min_words")) if request.form.get("min_words") else None
            max_words = int(request.form.get("max_words")) if request.form.get("max_words") else None
        except ValueError:
            min_words = None
            max_words = None
        keywords_list = [item.strip() for item in keywords.split(",") if item.strip()]
        page = _ensure_page(
            slug,
            topic=topic,
            keywords=keywords_list,
            host=request.host.split(":")[0],
            min_words=min_words,
            max_words=max_words,
            references=reference_urls,
            force=True,
        )
        source_label = "DeepSeek" if page.get("generator") == "deepseek" else "本地模板"
        flash(f"页面已生成（{source_label}）", "success")
        return redirect(url_for("admin_content"))

    @app.route("/admin/pages/<slug>/regenerate", methods=["POST"])
    def regenerate_page(slug: str):
        guard = _require_authentication()
        if guard:
            return guard
        page = _ensure_page(slug, host=request.host.split(":")[0], force=True)
        source_label = "DeepSeek" if page.get("generator") == "deepseek" else "本地模板"
        flash(f"页面已重新生成（{source_label}）", "success")
        return redirect(url_for("admin_content"))

    @app.route("/admin/pages/<slug>/delete", methods=["POST"])
    def delete_page(slug: str):
        guard = _require_authentication()
        if guard:
            return guard

        def _mutate(payload):
            payload.get("pages", {}).pop(slug, None)
            stats = payload.get("view_stats", {})
            if slug in stats:
                stats.pop(slug)

        update_data(_mutate)
        flash("页面已删除", "info")
        return redirect(url_for("admin_content"))

    @app.route("/admin/settings", methods=["POST"])
    def update_settings():
        guard = _require_authentication()
        if guard:
            return guard
        auto_count = request.form.get("auto_page_count", "12")
        keywords = request.form.get("default_keywords", "")
        model = request.form.get("deepseek_model", "deepseek-chat")
        language = request.form.get("language", "zh")
        ai_threads = request.form.get("ai_thread_count", "8")
        article_min = request.form.get("article_min_words", "800")
        article_max = request.form.get("article_max_words", "1500")
        ai_console_log = request.form.get("ai_console_log") == "on"
        keyword_list = [item.strip() for item in keywords.split(",") if item.strip()]

        def _mutate(payload):
            settings = payload.setdefault("settings", {})
            settings.update(
                {
                    "auto_page_count": int(auto_count or 12),
                    "default_keywords": keyword_list,
                    "deepseek_model": model,
                    "language": language,
                    "ai_thread_count": max(1, int(ai_threads or 8)),
                    "article_min_words": max(200, int(article_min or 800)),
                    "article_max_words": max(400, int(article_max or 1500)),
                    "ai_console_log": ai_console_log,
                }
            )
            if settings["article_max_words"] <= settings["article_min_words"]:
                settings["article_max_words"] = settings["article_min_words"] + 200

        update_data(_mutate)
        flash("设置已保存", "success")
        return redirect(url_for("admin_settings_page"))

    @app.route("/admin/auto-build", methods=["POST"])
    def auto_build():
        guard = _require_authentication()
        if guard:
            return guard
        count = int(request.form.get("count", 5))
        random_mode = _is_enabled(request.form.get("random"))
        host = request.host.split(":")[0]
        data = load_data()
        settings = data.get("settings", {})
        max_workers = max(1, int(settings.get("ai_thread_count", 8) or 8))
        generated = []
        jobs = []
        if random_mode:
            for _ in range(min(count, 30)):
                topic, keywords, slug = _random_theme(settings, data.get("domains", []), host)
                jobs.append({"slug": slug, "topic": topic, "keywords": keywords})
        else:
            jobs = [
                {"slug": slugify(f"pool-{random.randint(1000, 9999)}"), "topic": None, "keywords": None}
                for _ in range(min(count, 30))
            ]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _ensure_page,
                    job["slug"],
                    topic=job.get("topic"),
                    keywords=job.get("keywords"),
                    host=host,
                    force=True,
                ): job["slug"]
                for job in jobs
            }
            for future in as_completed(futures):
                try:
                    page = future.result()
                    generated.append(page.get("title", futures[future]))
                except Exception:
                    generated.append(futures[future])
        flash(f"已批量生成 {len(generated)} 个页面", "success")
        return redirect(url_for("admin_content"))

    @app.route("/admin/auto-build/stream")
    def auto_build_stream():
        guard = _require_authentication()
        if guard:
            return guard

        try:
            count = int(request.args.get("count", 5))
        except (TypeError, ValueError):
            count = 5
        count = max(1, min(count, 30))
        host = request.host.split(":")[0]
        data = load_data()
        settings = data.get("settings", {})
        random_mode = _is_enabled(request.args.get("random"))
        max_workers = max(1, int(settings.get("ai_thread_count", 8) or 8))
        article_min = max(200, int(settings.get("article_min_words", 800) or 800))
        article_max = max(article_min + 200, int(settings.get("article_max_words", article_min + 400) or article_min + 400))

        def _generate():
            yield "retry: 3000\n"
            jobs = []
            if random_mode:
                for _ in range(count):
                    topic, keywords, slug = _random_theme(settings, data.get("domains", []), host)
                    jobs.append({"slug": slug, "topic": topic, "keywords": keywords})
            else:
                jobs = [
                    {"slug": slugify(f"pool-{random.randint(1000, 9999)}"), "topic": None, "keywords": None}
                    for _ in range(count)
                ]
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _ensure_page,
                        job["slug"],
                        topic=job.get("topic"),
                        keywords=job.get("keywords"),
                        host=host,
                        min_words=article_min,
                        max_words=article_max,
                        force=True,
                    ): job
                    for job in jobs
                }
                for idx, future in enumerate(as_completed(futures)):
                    job = futures[future]
                    slug = job["slug"]
                    try:
                        page = future.result()
                    except Exception:
                        page = {"title": slug, "slug": slug, "excerpt": "生成失败"}
                    payload = {
                        "progress": idx + 1,
                        "total": count,
                        "title": page.get("title", slug),
                        "slug": slug,
                        "topic": job.get("topic"),
                        "updated_at": page.get("updated_at"),
                        "generator": page.get("generator", "unknown"),
                        "preview": (page.get("excerpt") or page.get("topic") or "")[:80],
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'status': 'done'})}\n\n"

        response = Response(stream_with_context(_generate()), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/api/pages")
    def api_pages():
        host = request.host.split(":")[0]
        data = load_data()
        pages = [
            {
                "slug": slug,
                "title": info.get("title"),
                "updated_at": info.get("updated_at"),
                "views": data.get("view_stats", {}).get(slug, 0),
                "host": info.get("host", host),
            }
            for slug, info in data.get("pages", {}).items()
        ]
        return jsonify({"pages": pages})

    return app
