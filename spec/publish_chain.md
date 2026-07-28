# 发布链路契约

`produce.py` 出完片之后，产物要走完「Release → 站点索引 → 手机页」这条链才算发出去。
这份文档是这条链上各段的契约：谁生成什么、谁搬到哪、页面从哪取。

出片本身（选帧、裁切、阈值、阶段顺序）见 [`docs/pipeline.md`](../docs/pipeline.md)，
这里不重复。

## 全景

```
produce.py
  └─ deliver/<slug>/[ep01/]final.mp4, cover_16x9.jpg, cover_9x16.jpg, meta.json
  └─ deliver/<slug>/queue.json                        ← 第一章
        │
        ├─ scripts/release_assets.py  摊平改名 → _release/  → gh release upload
        │     tag = clips-<slug>；ep01.mp4 / ep01_cover_16x9.jpg / ep01_cover_9x16.jpg
        │
        └─ scripts/update_site_index.py
              ├─ 16:9 封面复制进 site/covers/<slug>/<id>_16x9.jpg   ← 第三章
              ├─ 合并进 site/data/index.json                        ← 第二章
              └─ 回收没被索引引用的封面
                    │
                    └─ git commit site/data/index.json + site/covers/
                          └─ Cloudflare Pages 自动部署 → tougao.pages.dev
```

## 第一章：`queue.json`

`produce.py` 的 `build_queue()` 产出，是这条链唯一的上游真相。每集一条：

```jsonc
{
  "id": "ep01",
  "title": "…", "desc": "…", "tags": ["…"],
  "duration_sec": 266.28,
  "files": {                       // Release 上的扁平资产名
    "video": "ep01.mp4",
    "cover_16x9": "ep01_cover_16x9.jpg",
    "cover_9x16": "ep01_cover_9x16.jpg"
  },
  "urls": {                        // 一律是 Release 绝对地址
    "video":      "https://github.com/<repo>/releases/download/clips-<slug>/ep01.mp4",
    "cover_16x9": "https://github.com/<repo>/releases/download/clips-<slug>/ep01_cover_16x9.jpg",
    "cover_9x16": "https://github.com/<repo>/releases/download/clips-<slug>/ep01_cover_9x16.jpg"
  },
  "sha256": { "video": "…" },
  "scheduled_date": "2026-07-28",
  "status": "pending"
}
```

**`queue.json` 不含站内相对路径。** 它同时喂给 Release 正文和 B 站投稿脚本，
那两处都需要能直接点开的绝对地址。相对路径是站点侧的事，见第三章。

每集产物的目录：单集直接落在 `deliver/<slug>/`，多集落在 `deliver/<slug>/ep01/`。
判定收在 `release_assets.episode_source_dir()` 一处，`update_site_index.py` 直接复用
同一个函数，两边不会各写一份然后跑偏。

## 第二章：`site/data/index.json`

手机页唯一的数据源。页面读这个静态文件而**不打 GitHub API** —— 未登录
60 次/小时/IP 的限流一到，页面就白屏。

```jsonc
{
  "schema": 1,
  "updated_at": "2026-07-27T18:49:36Z",
  "episodes": [ { /* queue.json 的一集，另附 slug 和 speaker */ } ]
}
```

合并规则（`scripts/update_site_index.py`）：

- 扁平数组，每条附 `slug` 和 `speaker`
- 同 `slug` + `id` 视为同一条，重跑**覆盖**旧记录而不是追加
- 整表按 `scheduled_date` 升序；同一天内按 `slug` + `id` 稳定排
- **只增不删**：批次从 4 集重跑成 2 集时，ep03/ep04 仍留在索引里

## 第三章：封面由站点自己托管

### 为什么

封面原先直接引用 Release 资产地址。那个地址会 302 跳到
`release-assets.githubusercontent.com`，并且带 `Content-Disposition: attachment`
和 `Content-Type: application/octet-stream`。大陆 iPhone 上封面**经常加载不出来**。

现在封面改由 Pages 站点同源托管，走 Cloudflare CDN，不再依赖 GitHub 资产域名。

### 约定

| | 放在哪 | `index.json` 里怎么写 |
| --- | --- | --- |
| 16:9 封面 | 仓库 `site/covers/<slug>/<id>_16x9.jpg` | 站内相对路径 `covers/<slug>/<id>_16x9.jpg` |
| 9:16 封面 | 只在 Release 上 | Release 绝对地址（页面不用） |
| 视频 | 只在 Release 上 | Release 绝对地址 |

**视频永远不进仓库。** 一条成片 20–40 MB，提交进去仓库很快就没法用了；
封面一张 90–120 KB，四张一批约 0.4 MB，这个量可以接受。

相对路径以 `site/index.html` 所在目录为基准 —— Cloudflare Pages 的
Build output directory 就是 `site`，所以页面上 `covers/…` 解析成 `/covers/…`。

`site/covers/` 是**流水线产出的派生目录**，不要手工往里放东西：不被索引引用的
文件会在下一次运行时被回收。

### 生成方式没变

这一章只规定「封面文件放在哪、页面从哪取」。选帧、裁切、候选池、VLM 校验、
标题烧制**一个像素都没动**，`update_site_index.py` 对封面只做 `shutil.copyfile`。

### 保留策略

每次运行 `update_site_index.py`，合并完索引后回收一次 `site/covers/`：

> **只保留当前 `index.json` 引用到的那些 jpg，一张不多。**

- 集的 `id` 变了、或者有人把记录从 `index.json` 里摘掉 → 对应封面被删
- 某个 slug 整个下线 → 它的目录清空后一并删掉
- 记录的封面仍是 `http` 绝对地址（旧数据）→ 站点目录里本来就没有它的文件，不受影响
- 只删 `*.jpg`，只在 `site/covers/` 下递归

因为索引是「只增不删」的，正常出片节奏下封面数量等于索引条数，仓库线性增长且可预测。
要真正瘦身，先从 `index.json` 里删记录，下一次运行就会把对应封面回收掉。

### 声明了封面却找不到文件 = 硬失败

`files.cover_16x9` 有值但 `deliver/<slug>/[ep01/]cover_16x9.jpg` 不在，
`update_site_index.py` 退 1，**不写索引**。缺封面的页面只会剩个灰块，
不如在流水线里就停下来。

没声明 `files.cover_16x9` 的 queue（早期数据）原样放过，`urls` 不改写。

## 第四章：提交回仓库

`produce.yml` 的「合并进站点索引并提交」这一步，只在 `main` 上跑：

```bash
python scripts/update_site_index.py \
  --queue "deliver/$SLUG/queue.json" \
  --index site/data/index.json \
  --site-root site
mkdir -p site/covers
git add -A site/data/index.json site/covers    # -A 才能把回收掉的封面记成删除
git diff --cached --quiet && exit 0            # 没变化就不提交
git commit -m "chore(site): 更新 $SLUG 的发布索引与封面"
```

**只提交这两个路径**，`deliver/` 不进仓库。

matrix 里几条 job 会同时往 `main` 上推，撞车就 `git pull --rebase --autostash`
重试，最多 5 次。因为封面文件名按 slug 分目录，不同 slug 的 job 改的是不相交的
文件集，rebase 不会产生冲突。

## 前端契约

`site/app.js` 的 `normalize()` 把 `urls.cover_16x9` 原样取成 `ep.cover`，
**不区分相对还是绝对** —— 相对路径浏览器按文档基准解析，绝对地址照旧直连，
所以旧数据不用迁移也能显示。

封面位有两级兜底（`site/index.html` 的 `card-tpl`）：

- 没有 `urls.cover_16x9` → `<img>` 隐藏，露出 `.thumb-fallback` 灰块「暂无封面」
- 有地址但加载失败（`img` 的 `error` 事件）→ 同样切到灰块，不显示破图图标

两条都覆盖在 `site/README.md` 的容错清单里。
