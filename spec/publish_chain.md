# 发布链路契约

一次「一键出片」产出一个**批次**。批次是这条链路上唯一的取舍单位：

| 概念 | 载体 |
| --- | --- |
| 批次标识 | `slug`（一次运行一个） |
| 产物 | GitHub Release，tag 为 `clips-<slug>` |
| 索引条目 | `site/data/index.json` 里 `slug` 等于它的那几条 |

一个 slug ↔ 一个 Release ↔ 索引里的一组集，三者一一对应。

```
produce.py            → deliver/<slug>/queue.json + 成片
release_assets.py     → 摊平成 Release 资产
gh release create     → clips-<slug>
update_site_index.py  → 并进 index.json，并下架超窗口的旧批次
prune_releases.py     → 删掉索引里已经没有的 clips-* Release（默认不删）
```

## 为什么要下架

批次只增不减的话，同一段素材换个 slug 重跑就会在手机页上留下两批重复内容 ——
只有封面排版不同。更糟的是「今天该发这条」按 `scheduled_date` 挑，可能挑中旧
批次，点下载拿到的是上一版封面。所以合并这一步必须同时负责下架。

## 保留策略

`scripts/update_site_index.py` 在合并完之后立刻裁剪：**索引只保留最近
`KEEP_BATCHES` 个批次，默认 3**。

选 3 的理由：一批 2 集、排期每天一条，3 批约等于一周的存量。手机页的「往期」
还能翻到上一轮素材做对比，又不至于让重复内容一直堆着。改这个值用
`--keep-batches`，`KEEP_BATCHES` 常量是默认值。

两条不变量：

- **整批留或整批走。** 取舍以 slug 为单位，索引里不会出现只剩半批的情况。
- **重跑同一个 slug 不占新名额。** 它更新的是已有批次，不是第 4 批。

## 判定新旧的依据

`queue.json` 的 `generated_at` —— 合并时原样落到每条记录的 `batch_at` 字段，
批次的新旧取**批内最大的 `batch_at`**，同刻再按 slug 字典序兜底。

- 为什么不用 Release 创建时间：那要打 GitHub API，而合并这一步是纯本地的文件
  操作，不该为了排序引入网络依赖和限流风险。
- 为什么不用追加顺序：索引整表按 `scheduled_date` 排序，数组位置和批次先后
  没有关系。
- 为什么不用 `scheduled_date`：它是 `generated_at + index` 天算出来的派生值，
  同一天跑的两批会撞在一起 —— 而「同一天重跑」正是要解决的那个场景。
- 为什么取批内最大值：重跑只补了半批时，整批按最近一次合并算新，不会被半批
  旧记录拖成老批次。

`generated_at` 缺失时 `merge()` 直接抛错 —— 没有它就只能猜哪批该下架，猜错就是
静默丢数据。本次改动之前写进索引的旧记录没有 `batch_at`，按空串排在最老，也就是
最先被下架，这是符合预期的。

## 清理陈旧 Release

下架只把条目从 index.json 里摘掉，Release 还在仓库里占空间。
`scripts/prune_releases.py` 负责收尾。

```bash
# 只打印将要删除什么（默认）
python scripts/prune_releases.py --index site/data/index.json

# 真删，连 tag 一起删（不可逆）
python scripts/prune_releases.py --index site/data/index.json --execute
```

| 开关 | 作用 |
| --- | --- |
| `--index` | index.json 路径，必填 |
| `--execute` | 真的删除。不加就只报账 |
| `--repo` | `owner/name`，默认用 gh 当前仓库 |

四条安全线：

1. **默认 dry-run。** 删除不可逆，必须显式加 `--execute` 才会发出删除调用。
2. **只删索引里没有的。** 仍在 index.json 里的批次绝不会被碰。
3. **只认 `clips-` 前缀。** 别的用途的 Release（版本 tag 等）一律不管。
4. **对不上就报错。** 索引引用了一个不存在的 Release，说明索引和仓库已经不
   一致，这时候「谁是孤儿」不可信 —— 直接非零退出，不接着删别的。

### 删除走两条 REST 调用

删除**不用** `gh release delete --cleanup-tag`：同一个环境、同一个 token 下那条
子命令会 `HTTP 401: Requires authentication`，直接打 REST 却能过。所以每个孤儿
按顺序发两条：

```
DELETE /repos/{owner}/{repo}/releases/{release_id}    # 认 databaseId，不认 tag 名
DELETE /repos/{owner}/{repo}/git/refs/tags/{tag}      # 留下空 tag 下轮又会被当成孤儿
```

`release_id` 从 `gh release view --json databaseId` 拿，和读资产大小是同一次调用。
`gh api` 没有 `--repo`，不传 `--repo` 时靠 `{owner}/{repo}` 占位符落到当前仓库。

两步的失败分开归类：

| 情况 | 处理 | 退出码 |
| --- | --- | --- |
| Release 删除失败 | 立刻停手，不再删后面的 —— 多半是 token 权限问题，后面只会同样失败 | 1 |
| tag ref 删除失败 | 警告到 stderr，继续处理后面的孤儿 —— 占空间的 Release 已经删掉了，剩个空 ref 是收尾问题，不是整体失败 | 2 |
| tag ref 本来就不存在（404） | 不算错，目标状态已经达成 | 0 |

每个将删/已删的 Release 都会打印 tag、资产总大小、创建时间：

```
[prune] 将要删除（dry-run，未真删） 2 个 Release，共 68.4 MB：
  clips-munger_partner  35.0 MB  建于 2026-07-27T16:28:27Z
  clips-munger_chain  33.3 MB  建于 2026-07-27T15:33:43Z
[prune] dry-run 结束，加 --execute 才会真的删除
```

CI 里只跑 dry-run（produce.yml 的「列出可清理的陈旧 Release」一步），把账记在
运行日志里；真删是人工动作。
