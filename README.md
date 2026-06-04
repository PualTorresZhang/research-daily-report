# 研究院版日报自动生成与飞书推送系统

每天北京时间 10:07 自动采集“昨日”新闻，先扩容新闻池、再聚合议题簇、再生成研究院情报简报风格的 Markdown / HTML / JSON 日报，并通过飞书机器人推送摘要；10:22 设置一次兜底检查，若日报已存在则不重复推送。

## 项目结构

```text
.
├── config/
│   └── sources.yaml              # 信息源配置：RSS、网页、手动源
├── daily_research_report/
│   ├── cli.py                    # 命令行入口
│   ├── compiler.py               # 新闻池结构化、议题簇、链接分级、质量评分
│   ├── collectors.py             # RSS / Web / Manual 采集
│   ├── config.py                 # 配置加载
│   ├── dedup.py                  # URL 与标题去重
│   ├── feishu.py                 # 飞书机器人推送
│   ├── llm.py                    # 基于议题簇的 LLM 生成与降级模板
│   ├── models.py                 # 数据模型
│   ├── render.py                 # Markdown / HTML / JSON 渲染
│   └── time_utils.py             # 北京时间昨日窗口
├── reports/                      # 生成的日报文件
├── templates/
│   └── report.html.j2            # HTML 模板
├── .github/workflows/
│   └── daily-report.yml          # GitHub Actions 定时任务
├── .env.example
├── pyproject.toml
└── requirements.txt
```

## 配置环境变量

复制 `.env.example` 后按需配置：

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL=""
export LLM_API_KEY=""
export LLM_BASE_URL=""
export LLM_MODEL=""
export LLM_MAX_TOKENS="12000"
export FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export FEISHU_WEBHOOK_SECRET="可选，机器人启用签名校验时填写"
export NEWS_API_KEY="可选，MVP 暂未默认使用"
export OPENAI_MODEL="gpt-4.1-mini"
export REPORT_TIMEZONE="Asia/Shanghai"
```

默认使用 OpenAI API。也可以改用兼容 OpenAI Chat Completions 格式的其他模型服务：

```bash
export LLM_API_KEY="其他服务商 API Key"
export LLM_BASE_URL="https://api.example.com/v1"
export LLM_MODEL="provider-model-name"
export LLM_MAX_TOKENS="12000"
```

`LLM_*` 优先级高于 `OPENAI_*`。未配置任何可用 API key 时，系统仍会生成一个可运行的降级版日报，但不会做深度合并与情报化改写。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 默认生成北京时间昨天的日报
python -m daily_research_report.cli

# 指定日期
python -m daily_research_report.cli --date 2026-06-02

# 生成后推送飞书
python -m daily_research_report.cli --push
```

输出文件：

```text
reports/YYYY-MM-DD.md
reports/YYYY-MM-DD.html
reports/YYYY-MM-DD.json
```

## 生成 Pipeline

当前版本按 `research-daily compiler` 思路运行，不再是单条新闻摘要器：

1. **新闻池扩容**：按北京时间 T-1 日采集 P0 官方源、P1 补充源、P2 AI 源；候选新闻会保留标题、时间、来源、链接、正文摘要、实体、人名、机构、地点、领域标签、重要性分。
2. **去重与限额**：先按 URL/标题去重，再控制单一来源堆叠，例如商务部、OpenAI、BBC、央行、最高法、最高检都有每日上限。
3. **议题簇聚合**：按政策链条、人事/机构链条、事件链条、涉外链条、产业链条聚合，而不是只看标题相似。
4. **簇级生成**：LLM 接收的是结构化 `clusters`，每条正式稿件统一写成【事实】【背景/补充】【观察】【后续跟踪】。
5. **稳定栏目**：组织人事、反腐政法、涉台涉外、AI与科技产业不会消失；没有重大新闻时写“未见重大新增”，抓取失败时写“未成功抓取，需人工复核”。
6. **质量自检**：生成后给出官方源覆盖率、链接可验证率、议题簇合并率、重复新闻率、低价值资讯占比等指标。
7. **结构化输出**：`reports/YYYY-MM-DD.json` 保留候选新闻、议题簇、来源等级、合并理由、置信度、质量评分和 benchmark 差距。

链接等级：

- `A`：官方原文，如国务院、部委、外交部、纪委监委、最高法最高检。
- `B`：新华社、人民日报、央视、地方党报。
- `C`：主流商业媒体/国际通讯社。
- `D`：转载源/聚合源，仅在没有更好来源时使用，不能作为唯一依据。

## 信息源配置

编辑 `config/sources.yaml` 增删源：

```yaml
sources:
  - name: OpenAI News
    category: 科技与产业
    type: rss
    url: https://openai.com/news/rss.xml
    enabled: true

  - name: 外交部
    category: 涉外
    type: web
    url: https://www.mfa.gov.cn/web/wjdt_674879/
    selector: a
    include_url_patterns:
      - "/web/wjdt_674879/"
    enabled: true

  - name: 手动补充
    category: 要闻
    type: manual
    enabled: true
    url: manual://items
    items:
      - title: 示例标题
        url: https://example.com/news
        published_at: "2026-06-02T09:00:00+08:00"
        summary: 示例摘要
```

MVP 支持四类源：

- `rss`：读取 RSS/Atom，按发布时间过滤到昨日。
- `web`：按 CSS selector 抓网页链接。网页通常没有可靠发布时间，因此默认进入候选池，交给 LLM 与人工复核。
- `api`：读取常见 JSON 新闻 API，支持 `articles/items/data` 列表结构，并用 `NEWS_API_KEY` 作为 `X-Api-Key` 请求头。
- `manual`：手动补充重要来源，适合 X/Twitter、付费墙外摘要、内部线索。

网页源可用 `include_url_patterns` / `exclude_url_patterns` 做 URL 正则过滤。采集器也会自动过滤首页、站点地图、备案号、`javascript:` 链接等非新闻项；URL 中能识别出 `YYYYMMDD` 日期的链接必须落在报告日当天。

## 飞书机器人配置

### 方式一：群自定义机器人 Webhook

1. 在飞书群中添加“自定义机器人”。
2. 复制 Webhook 到 `FEISHU_WEBHOOK_URL`。
3. 如果启用“签名校验”，复制密钥到 `FEISHU_WEBHOOK_SECRET`。
4. 本地执行：

```bash
python -m daily_research_report.cli --push
```

飞书卡片会推送日报摘要与 HTML 文件链接。若在 GitHub Actions 中运行，建议把 HTML 发布到 GitHub Pages 或对象存储，然后通过 `--public-url` 传入公开链接。

如果没有传入公网或内网可访问的日报 URL，卡片只展示摘要和本地文件路径，不会放一个无法打开的本地链接。可点击详情链接的运行方式如下：

```bash
python -m daily_research_report.cli --push --public-url "https://example.com/reports/2026-06-02.html"
```

也可以用环境变量：

```bash
export REPORT_PUBLIC_URL="https://example.com/reports/2026-06-02.html"
python -m daily_research_report.cli --push
```

GitHub Actions 中的飞书详情链接会自动追加 `?v=<run_id>`，避免打开到浏览器或 GitHub Pages 缓存里的旧日报。

### 方式二：飞书应用机器人

如果你手上是应用的 `app_id` / `app_secret`，需要再准备一个接收群的 `chat_id`：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_RECEIVE_ID="oc_xxx"
export FEISHU_RECEIVE_ID_TYPE="chat_id"
python -m daily_research_report.cli --push
```

飞书应用侧需要满足：

- 应用已开启机器人能力。
- 机器人已被加入目标群。
- 应用已开通发消息权限，例如 `im:message:send_as_bot` 或等价发送消息权限。
- `FEISHU_RECEIVE_ID` 是目标群的 `chat_id`，不是 `app_id`。

如果不推送到群，而是推送给个人，把接收者类型改成个人标识即可：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_RECEIVE_ID="ou_xxx"
export FEISHU_RECEIVE_ID_TYPE="open_id"
python -m daily_research_report.cli --push
```

也可以用邮箱作为接收者：

```bash
export FEISHU_RECEIVE_ID="name@example.com"
export FEISHU_RECEIVE_ID_TYPE="email"
```

个人推送同样要求应用开启机器人能力、机器人对你可用，并具备发送消息权限。

## GitHub Actions 部署

1. 将项目推送到 GitHub 仓库。
2. 在仓库 `Settings → Secrets and variables → Actions` 添加：
   - `OPENAI_API_KEY`
   - 或者使用其他兼容模型：`LLM_API_KEY`
   - `FEISHU_WEBHOOK_URL`
   - `FEISHU_WEBHOOK_SECRET`，如需要
   - 或者使用应用机器人方式：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_RECEIVE_ID`
   - `NEWS_API_KEY`，如后续扩展新闻 API
3. 在仓库 Variables 中可选添加：
   - `LLM_BASE_URL`
   - `LLM_MODEL`
   - `LLM_MAX_TOKENS`，默认 `12000`
   - `OPENAI_MODEL`
4. 工作流 `.github/workflows/daily-report.yml` 已配置：
   - cron: `7 2 * * *`
   - 对应北京时间每天 `10:07`
   - cron: `22 2 * * *`
   - 对应北京时间每天 `10:22` 兜底，若日报已存在则跳过
5. 也可以在 Actions 页面手动触发，并输入 `report_date`。

## 本地 crontab 方案

```cron
7 10 * * * cd /path/to/research-daily-report && /path/to/python -m daily_research_report.cli --push >> logs/daily.log 2>&1
```

注意 crontab 的机器时区需要是北京时间；如果不是，请换算时间或设置 `TZ=Asia/Shanghai`。

## MVP 边界

- 网页源缺少发布时间时无法严格保证“只写昨天”，因此建议优先添加 RSS/API 源。
- X/Twitter 源建议作为 `manual` 或后续 API 扩展，且必须标注“未必经官方确认”。
- Bloomberg/FT 等付费墙内容仅使用公开可访问内容，避免抓取不可访问正文。
- 后续可扩展数据库缓存、全文抓取、向量聚类、实体库、人事库、案件库和更严格的链接可达性校验。
