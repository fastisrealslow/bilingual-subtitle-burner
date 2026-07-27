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

## 约束

- **`slug` 全局唯一**。重复的话两个 job 会抢同一个 artifact 名字，`plan_matrix.py` 会直接报错拦下。
- `translator` 只能是上表里的两个值，写错会在 `plan` 阶段失败，不会浪费 40 分钟的 runner。
- 缺 `source` 或 `slug` 同样在 `plan` 阶段就失败。

## 本地校验

push 之前可以先在本地跑一遍任务汇总，确认 JSON 能被正确解析：

```bash
python3 .github/scripts/plan_matrix.py
```

它会打印将要展开的 matrix。退出码非 0 就说明有配置错误。
