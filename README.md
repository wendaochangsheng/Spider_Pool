# 内容矩阵调度面板

一个面向多域内容编排的内部面板，具备自动伪原创生成、动态互链、外部链接插槽、域名池管理及后台控制台。所有页面默认通过 `robots.txt` 禁止搜索引擎收录，便于在内网或受控环境中搭建内容观测站。存储已经切换为 SQLite，支持并发访问、访问量防丢失合并以及爬虫识别。

## 功能速览

- ⚙️ 多域名管理：在后台添加任意泛解析到程序的域名，按主题分组。
- 📝 动态内容：访问 `/p/<slug>` 即会触发 DeepSeek API（或本地伪原创 fallback）生成长文，自动缓存并互链。
- 🔗 外部插槽：可维护一组外部链接，系统会优先在文章末尾植入；如果没有外链会自动互链池内页面。
- 📈 访问统计：每个页面都会累计访问次数，后台可查看热点。
- 🧰 后台控制台：固定账户 `admin/admin`，批量生成页面、管理设置、查看数据。
- 🕷️ 爬虫识别：自动记录常见搜索引擎 / 抓取 UA，并在后台展示来源与次数。
- 🚫 robots.txt：统一返回 `User-agent: *\nDisallow: /`，禁止任何搜索引擎收录。

## 快速开始

1. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```
2. **配置环境变量（可选）**
   ```bash
   export DEEPSEEK_API_KEY="sk-ee3cfc8aa37446c78e0aa91a6363873f"
   export DEEPSEEK_API_URL="https://api.deepseek.com/chat/completions"  # 可自定义
   export SPIDERPOOL_SECRET="随机字符串"  # Flask session 密钥
   export SPIDERPOOL_ADMIN="admin"       # 如需修改后台账户
   export SPIDERPOOL_PASSWORD="admin"
   ```
   未配置 `DEEPSEEK_API_KEY` 时，会自动走内置伪原创 fallback，方便离线调试。
3. **运行**
   ```bash
   python app.py
   ```
   服务器默认监听 `0.0.0.0:8000`，可通过 `PORT` 环境变量覆盖。
4. **后台入口**：访问 `http://localhost:8000/admin`，使用 `admin/admin` 登录即可。后台分为“概览 / 内容生成 / 配置中心”三块。

## 目录结构

```
Spider_Pool/
├── app.py                # 程序入口
├── spiderpool/           # 核心逻辑
│   ├── app_factory.py    # Flask 应用与路由
│   ├── content.py        # DeepSeek 接入与伪原创生成
│   ├── links.py          # 内外链编排逻辑
│   └── storage.py        # SQLite 存储、统计、防丢日志
├── templates/            # 前台与后台页面
├── static/css/           # 样式
├── data/site_data.db     # SQLite 数据库（首次启动会自动创建）
└── requirements.txt
```

## DeepSeek 接入说明

- 需要在环境变量设置 `DEEPSEEK_API_KEY`。
- 可通过 `DEEPSEEK_API_URL` 与 `DEEPSEEK_MODEL`（或后台设置 DeepSeek 模型字段）控制调用的模型与地址。
- 请求体遵循 Chat Completions 格式，系统会强制 DeepSeek 输出结构化 JSON，随后拼装为 HTML。
- 失败或超时时，系统会降级生成本地伪原创内容，保证页面永不空白。

## 管理后台能力

- 概览：访问热度、防丢访问统计、爬虫来源、最近 AI 日志。
- 内容生成：多线程批量生成（默认 8 线程，可配置）、单页生成及再生成、字数范围控制、参考 URL 注入。
- 配置中心：域名池、外链池、关键词与模型设置，实时同步页面互链。

## robots.txt

应用内置 `/robots.txt` 路由，固定返回：
```
User-agent: *
Disallow: /
```
确保所有搜索引擎都不会索引页面内容。

## 开发提示

- 所有数据存储在 `data/site_data.db`（SQLite），可自行定期备份或迁移到独立数据库。
- 若需引入更多定制内容，可在 `spiderpool/content.py` 中调整 prompt 与 fallback 模板。
- 若要对接其他 API，可在 `generate_article` 中扩展。

欢迎根据业务策略继续扩展，例如加入抓取日志、更多模板等。
