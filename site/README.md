# 手机投稿页（Cloudflare Pages）

纯静态页面，无构建步骤。手机竖屏优先，用来在手机上按排期逐条下载视频、复制标题/标签/简介，再手动投稿。

## 目录结构

```
site/
  index.html                 页面骨架
  style.css                  样式
  app.js                     原生 JS，无框架、无 CDN 依赖
  data/index.json            唯一数据源（CI 会覆盖成真数据）
  assets/sample_cover_*.jpg  示例封面，仅供本地预览
```

## 本地预览

必须用 HTTP 打开（`file://` 下 `fetch` 会被浏览器拦截）：

```bash
cd site
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000
```

## Cloudflare Pages 部署设置

在 Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git，选中本仓库后按下表填：

| 项 | 值 |
| --- | --- |
| Production branch | `main` |
| Framework preset | `None` |
| Build command | 留空 |
| Build output directory | `site` |
| Root directory | 留空（仓库根） |
| Environment variables | 无 |

保存后每次 `main` 有提交就会自动重新部署。CI 把新 Release 的集数合并进 `site/data/index.json` 并提交后，页面会自动刷新。

## 数据契约

`data/index.json` 见规格第二章：

```json
{ "schema": 1, "updated_at": "…", "episodes": [ { "…episode…", "slug": "…", "speaker": "…" } ] }
```

页面对数据做了容错，以下情况都不会白屏：

- 文件缺失、HTTP 错误、JSON 解析失败 → 显示加载失败提示
- `episodes` 为空数组或字段不是数组 → 显示「暂无可发布的集数」
- 缺 `urls` 或缺 `urls.video` → 下载按钮变灰显示「暂无视频直链」
- 缺 `urls.cover_16x9` 或封面加载失败 → 缩略图位显示「暂无封面」占位
- 缺 `title` / `tags` / `desc` → 标题回退为「（无标题）」，对应复制按钮置灰
- 缺或非法 `scheduled_date` → 显示「排期未定」，排在列表末尾

## 页面行为

- 顶部大卡片是「今天该发这条」（`scheduled_date` 等于今天且未发布）；今天没有排期时退化为「最近一条待发」。
- 中部「待发列表」按 `scheduled_date` 升序列出其余未过期集数。
- 底部「往期」（排期早于今天）默认折叠。
- 三个复制按钮优先用 `navigator.clipboard`，失败时回退到隐藏文本域选中 + `execCommand('copy')`，覆盖 iOS Safari 与非 HTTPS 环境。
