# sources/ — 批量出片的任务定义

往这个目录里 push 一个 `*.json`，`.github/workflows/produce.yml` 就会被触发，
每条任务用 matrix 并发出片，产物走 workflow Artifacts（`deliver-<slug>`，留存 90 天）。

> ⚠️ 目录里**故意不放示例 `.json`**。任何 `sources/*.json` 的改动都会触发真实出片，
> 会实际下载视频并调用 SiliconFlow。要试跑请自己新建一个，别把测试文件长期留在仓库里。

## Schema

单条任务：

```json
{
  "source": "https://www.youtube.com/watch?v=xxxxxxxxxxx",
  "slug": "munger-2023",
  "title_override": "芒格谈耐心",
  "translator": "deepseek-v3",
  "dual": false,
  "speaker": "查理·芒格",
  "sub_mode": "zh-only"
}
```

一个文件里也可以放一个数组，批量定义多条：

```json
[
  { "source": "https://archive.org/details/xxxx", "slug": "buffett-1998" },
  { "source": "https://www.youtube.com/watch?v=yyyy", "slug": "dalio-principles" }
]
```

| 字段 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `source` | ✅ | — | 视频 URL（yt-dlp 支持的平台，archive.org 也能命中）或仓库内的相对路径 |
| `slug` | ✅ | — | 产物目录名 `deliver/<slug>/`，同时是 artifact 名。只允许字母数字 `.` `_` `-` |
| `title_override` | ❌ | `""` | 留空则让模型起一个 15 字以内的标题 |
| `translator` | ❌ | `deepseek-v3` | `deepseek-v3` 或 `claude-sonnet-4.6`（后者需要 `ANTHROPIC_API_KEY`，CI 里没注入） |
| `dual` | ❌ | `false` | 两个翻译都跑，额外产出 `compare_grid.jpg` 对比拼图 |
| `cover_time_sec` | ❌ | `""` | 手动钉死封面帧的时间点（秒），跳过人脸预筛和 VLM 校验。留空走自动选帧 |
| `cover_crop` | ❌ | `""` | 封面选帧裁切 `W:H:X:Y`（同 ffmpeg 的 crop 滤镜），留空不裁 |
| `speaker` | ❌ | `""` | 说话人名字，用于金句打分和封面左上角红标。留空用 `produce.py` 的默认值「演讲者」 |
| `sub_mode` | ❌ | `both` | `both` 烧中英双语；`zh-only` 只烧中文，源片自带的英文硬字幕当英文轨用 |
| `sub_margin_v` | ❌ | `""` | `zh-only` 时中文距底边的像素，或 `auto` 逐条 cue 自动避让。留空用 `produce.py` 默认 `auto` |
| `sub_avoid_gap` | ❌ | `""` | `sub_margin_v: auto` 时中文块底边与源片硬字幕带上沿之间的像素间隙。留空用默认 `24` |

### 什么时候需要 `cover_time_sec`

解说式剪辑（主讲人原声 + 素材空镜）全片可能根本没有主讲人的正脸。这种源片
自动选帧只会挑到不相干的素材人物，拿它冒充主讲人属于误导，所以出片会直接
退 2 而不是硬凑。此时用 `cover_time_sec` 指定一个能表意的空镜时间点即可。

### 什么时候需要 `cover_crop`

很多 YouTube / archive.org 的转录源片底部烧死了一条英文硬字幕。封面选帧是直接
截原帧，这条英文字幕就会原样留在成品封面上。`cover_crop` 在截帧时先裁掉那一
条：候选帧（人脸预筛、VLM 校验）和最终出图用的是同一个裁切，不会出现「预筛看
到的画面和成品不是同一张」。

先用播放器量出字幕带的上沿 y，再按 `宽:上沿y:0:0` 填。例如 854x480 的源片、
英文字幕落在 y=408 以下，填 `854:396:0:0`（留一点余量）。

### 什么时候需要 `sub_mode: zh-only`

很多源片自带烧死的英文硬字幕。默认 `both` 会在它之上再烧一层 EN + 一层 ZH，
同一屏三层文字互相压字，成片没法看。`zh-only` 只烧中文，并把它抬到源片那条
硬字幕的正上方 —— 源片自带的英文直接当英文轨用，出来是一行中文 + 一行英文。

摆位靠 `sub_margin_v`（中文距画面底边的像素），默认 `auto`。

**为什么默认自动挡**：源片硬字幕的位置往往不是全片恒定的。854x480 的芒格源片
常规对白落在 y=408~456，但引言板那几段换了明显更大的字号、排两行、整体位置抬
到 y≈330~400。全片钉死一个值必然二选一 —— 按对白摆就会被引言板压住（run
30263406353 的成片 00:45 处就是这样，中文行正好糊在 `AS POSSIBLE` 上）。

`auto` 逐条 cue 抽几帧、统计画面下半部分的亮文字带，取该 cue 期间最靠上的那次
上沿 y，按 `画面高 - 上沿y + sub_avoid_gap` 单独算这一条的摆位。探不到硬字幕
的 cue 回落到 `96`，日志里会写明是回落。

`sub_avoid_gap` 默认 `24` —— `96` 那个老默认值就是 `480-408+24` 来的。

要复现改动前的固定摆位（或自动挡在某条源片上判错了），把 `sub_margin_v` 填成
整数即可，行为与自动挡完全无关。自动挡如果算出来的摆位会把中文顶到画面上半
部分（源片硬字幕异常高，或检测把画面主体误判成文字），出片直接退 2 而不是硬
出，退出时的 JSON 里带上 cue 索引、检测到的 y 和算出的 MarginV。

每条 cue 的实际摆位都记进 `meta.json` 的 `sub_placements`，事后核对叠字问题不
用重跑探测。

`sub_mode` 只管成片字幕，封面上的那条硬字幕要另外用 `cover_crop` 裁掉，两个
字段通常一起填。

## 约束

- **`slug` 全局唯一**。重复的话两个 job 会抢同一个 artifact 名字，`plan_matrix.py` 会直接报错拦下。
- `translator` 只能是上表里的两个值，写错会在 `plan` 阶段失败，不会浪费 40 分钟的 runner。
- `cover_time_sec` 写了非数字或负数同样在 `plan` 阶段就失败。
- `cover_crop` 不是 `W:H:X:Y` 四个非负整数时同样在 `plan` 阶段就失败。
- `sub_mode` 只能是 `both` 或 `zh-only`；`sub_margin_v` 只能是 `auto` 或非负整数；`sub_avoid_gap` 必须是非负整数。写错同样在 `plan` 阶段就失败。
- 缺 `source` 或 `slug` 同样在 `plan` 阶段就失败。

## 本地校验

push 之前可以先在本地跑一遍任务汇总，确认 JSON 能被正确解析：

```bash
python3 .github/scripts/plan_matrix.py
```

它会打印将要展开的 matrix。退出码非 0 就说明有配置错误。
