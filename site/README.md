# 手机投稿页（Cloudflare Pages）

纯静态页面，无构建步骤。手机竖屏优先，用来在手机上按排期逐条下载视频和封面、复制标题/标签/简介，再手动投稿。

## 目录结构

```
site/
  index.html                 页面骨架
  style.css                  样式
  app.js                     原生 JS，无框架、无 CDN 依赖
  data/index.json            唯一数据源（CI 会覆盖成真数据）
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
{ "schema": 1, "updated_at": "…", "episodes": [
  { "…episode…", "slug": "…", "speaker": "…",
    "batch_at": "2026-07-27T12:00:00Z", "release_tag": "clips-…" } ] }
```

`slug` 相同的几条是同一个批次（一次出片运行），`batch_at` 是这批的生成时间，
`release_tag` 是它对应的 Release。页面不读这两个字段，它们是给下架逻辑用的。

CI 每次合并新批次时只保留最近 3 个批次，更早的整批从这个文件里移出，所以同一段
素材换个 slug 重跑不会在页面上留下两批重复内容。保留策略、判定新旧的依据、以及
陈旧 Release 怎么清理，见 [`spec/publish_chain.md`](../spec/publish_chain.md)。

页面对数据做了容错，以下情况都不会白屏：

- 文件缺失、HTTP 错误、JSON 解析失败 → 显示加载失败提示
- `episodes` 为空数组或字段不是数组 → 显示「暂无可发布的集数」
- 缺 `urls` 或缺 `urls.video` → 下载按钮变灰显示「暂无视频直链」
- 缺 `urls.cover_16x9` 或封面加载失败 → 缩略图位显示「暂无封面」占位
- 缺 `urls.cover_16x9` / `urls.cover_9x16` → 对应的封面下载按钮置灰显示「横版 · 暂无」/「竖版 · 暂无」，不会渲染死链
- 缺 `title` / `tags` / `desc` → 标题回退为「（无标题）」，对应复制按钮置灰
- 缺或非法 `scheduled_date` → 显示「排期未定」，排在列表末尾

## 页面行为

- 顶部大卡片是「今天该发这条」（`scheduled_date` 等于今天且未发布）；今天没有排期时退化为「最近一条待发」。
- 中部「待发列表」按 `scheduled_date` 升序列出其余未过期集数。
- 底部「往期」（排期早于今天）默认折叠。
- 每张卡片的「下载视频」下方是「下载封面」两个按钮：「横版 · B站」取 `urls.cover_16x9`，「竖版 · 抖音」取 `urls.cover_9x16`。
- 顶部大卡片的复制按钮下方有一个默认收起的「存到相册（iPhone）」折叠块，说明怎么把下载好的视频和封面从「文件」App 存进相册（B 站 iOS 客户端投稿只能从相册选素材，封面图同理）。只在大卡片里出现一次，列表卡片没有。
- 三个复制按钮优先用 `navigator.clipboard`，失败时回退到隐藏文本域选中 + `execCommand('copy')`，覆盖 iOS Safari 与非 HTTPS 环境。
