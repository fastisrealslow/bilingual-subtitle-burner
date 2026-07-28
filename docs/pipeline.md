# 出片流水线与拒绝码

`produce.py` 把「一个视频源」跑成「一条 3 分钟带双语字幕的成片 + 两张封面」。
这份文档说明各阶段做了什么、在什么条件下会拒绝出片，以及拒绝时怎么排查。

## 阶段

| # | 阶段 | 做什么 | 产物 |
| --- | --- | --- | --- |
| 1 | `input` | URL 走 yt-dlp（带退避重试）；`file://` 和本地路径直接用 | `_tmp/<slug>/source.mp4` |
| 2 | `transcribe` | faster-whisper `base` 转写（CI 上不用 large-v3，太慢） | `_tmp/<slug>/transcript.srt` |
| 3 | `highlight` | Qwen3-8B 打分挑金句 → 切点吸附到句子边界 → **过出片门槛** | `_tmp/<slug>/quotes.json` |
| 4 | `cover-select` | 选帧（几何规则 + Qwen3-VL 校验）→ **过封面门槛** | `_tmp/<slug>/cover_tmp/` 里的选中帧 |
| 5 | `translate` | DeepSeek-V3 翻译选中窗口内的字幕 → 中文标点归一化 | `_tmp/<slug>/quotes_zh.<translator>.json` |
| 6 | `assemble` | 切三段 → 各自烧双语字幕 → concat | `deliver/<slug>/final.mp4` |
| 7 | `title` | 15 字以内标题（`--title-override` 可跳过） | 写进 meta.json |
| 8 | `cover-render` | 把标题烧到第 4 步选中的帧上，出两版封面 | `deliver/<slug>/cover_16x9.jpg`、`cover_9x16.jpg` |
| 9 | `manifest` | 汇总时长、seg 结构、模型版本、commit、SHA256 | `deliver/<slug>/meta.json` |

只有第 3、4、5、7 步会调 SiliconFlow。开跑时日志里会先打一行完整阶段顺序，
每进一步再打一行 `[stage] N/9 …`，跑到哪一步一眼可见。

### 为什么 `cover-select` 在 `translate` 前面

封面门槛是全流程里唯一一个会在后段把整条片子否掉的闸门。它原本排在最后，
于是每次退 2 都是**先把翻译的钱花光、成片也烧完**，再告诉你这条片子不能出
（CI run 30259127265、30260691746 就是这么白跑的）。

选帧和阈值判定不依赖译文，所以整块提到 `translate` 之前；只有「把标题烧到帧上」
真的依赖 `title`，那一步留在原位拆成 `cover-render`。`--cover-time-sec` 手动钉帧
那条路径同样提前 —— 时间点截不出帧、或钉下的帧过不了人物校验，都该早点知道。**阈值和拒绝条件一个都没动，
只改了顺序。**

### 为什么专名纠错做在 `translate` 而不是 `transcribe`

成片里的「巴特勒先生」看着像翻译问题，实际是第 2 步听错了：faster-whisper 把
音频转成了 `MR. BUTLER`，第 5 步只是忠实地把错的输入译对了。

在 `transcribe` 侧修，实测两条路都不通：

- **换更大的模型**：`base`→`Mr. Butler`、`small`→`Mr. Bob`、`medium`→`Mr. Bowman`，
  没有一个听对，只是换个错法，还把 CI 转写时间拖长。
- **加 `initial_prompt` / `hotwords`**：只影响开头，这段话在 154 秒处，
  早被前文上下文冲掉了；副作用是分段变粗（76 段→39 段，最长段 4.5s→9.8s），
  而 `split_segment()` 对英文原样返回，会直接产出超长字幕。

所以 `scripts/transcribe.py` 不动，纠错放在 `translate` 的系统提示词里 ——
那里已经注入了 `glossary.json` 对照表，模型手上同时有「听到的拼写」和
「本领域该有的人名」，是全流程唯一能做这个判断的位置。细节与残留风险见
README「语音识别听错人名」一节。

## 健壮性：缓存与重试

无人值守跑的时候，两件事最贵：**白跑一趟**，和**为同一份结果付两次钱**。

### LLM/VLM 响应缓存

所有 SiliconFlow 调用都经 `scripts/sf_client`，落到既有的 `scripts/sf_transport`
（curl 子进程）。缓存键是 `sha256(模型名 + 端点 + 规范化请求体)`，值是完整响应
JSON，落在仓库根 `.llm_cache/`：

- 命中直接返回，**一个请求都不发**；跑完打一行命中/未命中计数
- **只缓存成功响应**。把失败缓存下来，重跑会永远卡在同一个错误上
- 缓存文件带 `written_at` 和 `model`，排查时不用猜是哪一次写的
- `--llm-cache-dir DIR` 改目录，`--no-llm-cache` 关掉

这一层包住 highlight、translate、封面 VLM 三处。晚期失败重跑时，前面已完成的
付费调用一次都不用重发。CI 里 `produce.yml` 用 `actions/cache` 把 `.llm_cache/`
按 slug 挂上，跨 run 复用。

### 重试分类

盲目重试只会把真实原因拖到超时之后才暴露，所以按状态码分：

| 分类 | 状态码 / 情形 | 行为 |
| --- | --- | --- |
| 可重试 | 429、500、502、503、504、连接失败、超时、响应体空或非 JSON | 指数退避 + 抖动后重试 |
| 不可重试 | 400 请求体有问题、401/403 鉴权、402 余额不足 | 立即失败，退 1 |
| 不可重试 | 其它意外状态码（404 之类） | 立即失败，退 3 |

- 429 优先**尊重 `Retry-After`**，秒数和 HTTP 日期两种格式都解析；没有该头才退避
- 单次退避上限 30s，一个请求的**累计退避上限 60s**
- 尝试次数由 `--llm-max-retries` 配（含首次，默认 3）
- **超时更保守**：客户端超时时服务端可能已经算完并计了费，再发一遍就是花两份钱
  买一份结果，所以超时把上限压到 2 次，并在日志里写明原因

不可重试的失败会往 stderr 打结构化 JSON，带上 `http_status` 和 `reason`：

```json
{"stage": "translate", "reason": "insufficient_balance", "http_status": 402, "detail": "…"}
```

### yt-dlp 下载重试

archive.org 的 `HTTP Error 500` 是间歇性的（CI run 30263087066 就这么退 3 了，
原样重跑就过）。现在外层带退避重试（`--download-retries`，默认 3；
`--download-backoff-sec` 配退避基数），同时给 yt-dlp 自己带上 `--retries` 和
`--fragment-retries`。

404、私有视频、非法 URL 属于「重试也变不出来」，直接退 1（片源问题）；5xx、
超时、连接重置以及判不出来的错误按可重试处理。

## 退出码

| 码 | 含义 | 该怎么办 |
| --- | --- | --- |
| `0` | 成功 | — |
| `1` | 参数 / 配置错误 | 命令行参数不合法（拼错 flag、类型不对、缺必需参数）、缺 `SILICONFLOW_API_KEY`、slug 非法、片源不存在、缺 ffmpeg/yt-dlp |
| `2` | **内容质量不达标** | 这条片源挑不出够格的金句或封面。不是 bug，是拒绝硬出 |
| `3` | 外部依赖失败 | SiliconFlow 5xx / 全模型不可用、yt-dlp 下载失败、ffmpeg 非零退出 |

退 2 和退 3 要分清楚：**退 2 重试没有意义**（换片源或放宽阈值），退 3 通常重试就好。

**命令行参数错误一律退 1，不退 2。** argparse 默认把用法错误退 2，会和「内容质量
不达标」撞号 —— 敲错一个 flag，照上表读出来的结论是「换片源」。带退出码约定的入口
（`produce.py`、`scripts/highlight.py`、`scripts/publish_bilibili.py`、
`steps/step7_cover.py`）都用 `ConfigErrorArgumentParser` 覆盖了 argparse 的
`error()` 改走退 1，同时保留 argparse 原本「哪个参数错了」的文案。`-h/--help`
仍退 0。

## 拒绝原因（退出码 2）

拒绝时会往 stderr 打一行结构化 JSON，便于 CI 里直接 grep。

### highlight

```json
{"stage": "highlight", "reason": "insufficient_duration", "actual_sec": 87, "threshold_sec": 150, "selected": 3}
```

| `reason` | 触发条件 | 默认阈值 |
| --- | --- | --- |
| `insufficient_quotes` | 时长达标的金句条数不够 | `MIN_QUOTES = 3` |
| `insufficient_duration` | 前 3 段拼起来不到 2.5 分钟 | `MIN_TOTAL_SEC = 150` |
| `insufficient_episode_quotes` | `--episodes N` 时第 k 集凑不满 3 段 | `SEGMENTS = 3` |
| `insufficient_episode_duration` | `--episodes N` 时第 k 集总时长不够 | `MIN_TOTAL_SEC = 150` |
| `empty_transcript` | 转写结果为空 | — |

单段时长下限 `MIN_QUOTE_SEC = 15`：更短的片段先被筛掉，再去数条数。
总时长按**真正会拼进成片的那三段**算，不是全部候选之和 —— 拿候选池凑数没有意义。

### 段数闸门（`--episodes N`）

`--episodes N` 是**全有或全无**：N 集里有任意一集凑不出合格片段，整批退 2，
一集都不出。少出集看着像「省着用」，实际是另一种硬出 —— 调用方按 N 排好了 N 天的
发布档期，静默回 M<N 集会被下游当成正常结果收下。带 `insufficient_episode_*`
的两个 `reason` 里有 `episode`（卡在第几集）、`episodes_ready`（已凑齐几集）和
`episodes_requested`，够定位是源片太短还是集数要得太多。

阈值可用环境变量覆盖：

```bash
HIGHLIGHT_MIN_TOTAL_SEC=120 HIGHLIGHT_MIN_QUOTES=2 HIGHLIGHT_MIN_QUOTE_SEC=10 python produce.py ...
```

`--strict-highlights` 会**忽略这些环境变量**，只认代码里的下限。CI 上建议一直带着，
免得有人为了让流水线变绿偷偷把阈值调松。

### cover

```json
{"stage": "cover", "reason": "no_frame_passed_vlm", "rejected": 9, "threshold": 5, "min_cover_score": 6}
```

| `reason` | 触发条件 |
| --- | --- |
| `no_frame_passed_vlm` | Qwen3-VL 判定不合格的候选帧超过 5 个，且没有任何合格帧 |
| `no_frame_meets_geometry` | `--no-vlm` 路径下没有一帧满足几何规则 |
| `pinned_frame_rejected` | `--cover-time-sec` 钉下的帧未通过 VLM 人物校验（不是 `--speaker` 本人，或封面分低于 6） |

### 钉帧只覆盖选帧，不覆盖闸门

`--cover-time-sec` 早先连人物校验一起跳过，于是钉错时间点会**静默出片**：
CI run 30281699063（`munger_chain`，`--cover-time-sec 287`）产出的封面是
爱因斯坦的一张黑板资料照，却压着「查理·芒格」的角标，还发了 Release。

现在钉下的帧照样送 `call_vision_llm`，和自动选帧共用同一条判定
（`frame_passes_vlm`：必须是主讲人本人且封面分 ≥ `MIN_VLM_PASS_SCORE`）。
判定不过退 2 并给出 `pinned_frame_rejected`，带上被钉的时间点、VLM 的人物
判定与理由；VLM 给不出判定时退 3（`pinned_frame_verification_unavailable`）
—— 校验不了不等于校验通过。

确实要用未经核验的帧，必须显式加 `--cover-allow-unverified`（默认关闭），
此时 stdout/stderr 都会打出「封面帧未经 VLM 人物核验」的告警，
`meta.json` 里记 `cover_verification: "skipped"`。

几何规则三条（`--no-vlm` 时是唯一标准，走 VLM 时是送审前的预筛）：

1. 最大正脸框面积 ≥ 整帧的 5%（`MIN_FACE_AREA_RATIO`）
2. 人脸中心落在画面上 60%（`FACE_TOP_RATIO`）—— 下方要留给标题条
3. 人脸不压在四角水印区（`WATERMARK_MARGIN = 12%`）

VLM 路径下，判定不合格的帧连同原因会写进 `meta.json` 的 `cover_vlm_rejections`，
即使最终出片成功也会保留，便于回查选帧质量。走 `--no-vlm` 时 `cover_vlm_passed=false`。

## meta.json

```jsonc
{
  "slug": "munger-2023",
  "title": "芒格谈耐心",
  "source_url": "https://...",
  "duration_sec": 178.4,
  "resolution": "854x480",
  "segment_count": 3,
  "segments": [
    { "index": 1, "rank": 1, "score": 9.2,
      "source_start_sec": 189.31, "source_end_sec": 245.0,
      "duration_sec": 55.69, "cues": 14, "reason": "..." }
  ],
  "models": {
    "transcribe": "faster-whisper/base",
    "highlight": "Qwen/Qwen3-8B",
    "translate": "deepseek-ai/DeepSeek-V3",
    "translator": "deepseek-v3",
    "vision": "Qwen/Qwen3-VL-8B-Instruct"
  },
  "cover_vlm_passed": true,
  "cover_vlm_rejections": [],
  "commit": "<GITHUB_SHA 或 local>",
  "sha256": { "final.mp4": "…", "cover_16x9.jpg": "…", "cover_9x16.jpg": "…" }
}
```

## 中文标点

所有中文译文都过一遍 `platform_rules.normalize_cjk_punctuation`：

- 弯引号 / 半角引号 → 直角引号
- 中文语境下的半角标点 → 全角
- **外层 `『』` → `「」`**，嵌套在 `「」` 里的 `『』` 保持不变

分层规范是**外层 `「」`，内层 `『』`**。DeepSeek-V3 的译文经常在最外层直接写
`他只说『不行』`，这一道会把它纠正成 `他只说「不行」`。
覆盖用例见 `tests/test_punct_double_quotes.py`。
