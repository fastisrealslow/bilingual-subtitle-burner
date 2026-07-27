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
  "dual": false
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

## 约束

- **`slug` 全局唯一**。重复的话两个 job 会抢同一个 artifact 名字，`plan_matrix.py` 会直接报错拦下。
- `translator` 只能是上表里的两个值，写错会在 `plan` 阶段失败，不会浪费 40 分钟的 runner。
- `cover_time_sec` 写了非数字或负数同样在 `plan` 阶段就失败。
- `cover_crop` 不是 `W:H:X:Y` 四个非负整数时同样在 `plan` 阶段就失败。
- 缺 `source` 或 `slug` 同样在 `plan` 阶段就失败。

## 本地校验

push 之前可以先在本地跑一遍任务汇总，确认 JSON 能被正确解析：

```bash
python3 .github/scripts/plan_matrix.py
```

它会打印将要展开的 matrix。退出码非 0 就说明有配置错误。
