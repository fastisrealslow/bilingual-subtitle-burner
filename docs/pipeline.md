# 出片流水线与拒绝码

`produce.py` 把「一个视频源」跑成「一条 3 分钟带双语字幕的成片 + 两张封面」。
这份文档说明各阶段做了什么、在什么条件下会拒绝出片，以及拒绝时怎么排查。

## 阶段

| # | 阶段 | 做什么 | 产物 |
| --- | --- | --- | --- |
| 1 | `input` | URL 走 yt-dlp（带分类重试）；`file://` 和本地路径直接用 | `_tmp/<slug>/source.mp4` |
| 2 | `transcribe` | faster-whisper `base` 转写（CI 上不用 large-v3，太慢） | `_tmp/<slug>/transcript.srt` |
| 3 | `highlight` | Qwen3-8B 打分挑金句 → 切点吸附到句子边界 → **过出片门槛** | `_tmp/<slug>/quotes.json` |
| 4 | `title` | 15 字以内标题（`--title-override` 可跳过） | 写进 meta.json |
| 5 | `cover` | 选帧（几何预筛 + Qwen3-VL 打分 → **过阈值判定**）→ 渲染两版封面 | `deliver/<slug>/cover_16x9.jpg`、`cover_9x16.jpg` |
| 6 | `translate` | DeepSeek-V3 翻译选中窗口内的字幕 → 中文标点归一化 | `_tmp/<slug>/quotes_zh.<translator>.json` |
| 7 | `assemble` | 切三段 → 各自烧双语字幕 → concat | `deliver/<slug>/final.mp4` |
| 8 | `manifest` | 汇总时长、seg 结构、模型版本、commit、SHA256 | `deliver/<slug>/meta.json` |

只有第 3、4、5、6 步会调 SiliconFlow。

### 为什么封面排在翻译前面

翻译是整条流水线的主要开销，封面选帧则是最容易判死刑的一关。封面曾经排在最后，
结果 CI run `30259127265` 和 `30260691746` 两次都是走完翻译、走完 assemble，
最后卡在封面阈值上退 2 —— 翻译的钱已经全花掉，成片也白烧了。

现在的规则是**会拒的关卡排在花钱的关卡前面**：封面不达标就在翻译一个字都还没翻的
时候退出。手动钉帧（`--cover-time-sec`）走同一个位置。标题只依赖金句
（`transcript_zh` / `transcript_en` 都来自 highlight），不依赖译文，所以能跟着一起
提前。阈值和拒绝条件一个都没动，只改了顺序。

阶段顺序同时打在日志里（`[pipeline] 阶段顺序：…`）和 `--help` 的结尾。

## 退出码

| 码 | 含义 | 该怎么办 |
| --- | --- | --- |
| `0` | 成功 | — |
| `1` | 参数 / 配置错误 | 缺 `SILICONFLOW_API_KEY`、slug 非法、片源不存在、缺 ffmpeg/yt-dlp、**SiliconFlow 400/401/402/403**、**URL 格式不被 yt-dlp 支持** |
| `2` | **内容质量不达标** | 这条片源挑不出够格的金句或封面。不是 bug，是拒绝硬出 |
| `3` | 外部依赖失败 | SiliconFlow 限流/5xx 重试耗尽、全模型不可用、片源 404/已下架、下载重试耗尽、ffmpeg 非零退出 |

退 2 和退 3 要分清楚：**退 2 重试没有意义**（换片源或放宽阈值），退 3 通常重试就好。
退 1 里新归进来的那几类（密钥错、余额不足、URL 非法）同样**重试没有意义** ——
它们要么改密钥要么充值要么改 URL，自动重试只会把真实原因埋进一堆重试日志里。

## LLM 响应缓存

所有 SiliconFlow 调用（highlight、translate、封面 VLM）都经过 `scripts/sf_client`，
它在 `scripts/sf_transport`（curl 子进程）外面包了一层内容寻址的磁盘缓存。

- 键 = `sha256(模型名 + 端点 + 请求体的规范化 JSON)`，值 = 完整响应 JSON
- 默认落在仓库根的 `.llm_cache/`；`--llm-cache-dir` 改目录，`--no-llm-cache` 关掉
- 命中直接返回，**一个请求都不发**；每次调用打一行命中/未命中，收尾打一行汇总
- **只缓存成功响应**，失败绝不落盘 —— 否则一次 500 会被永久钉死
- 缓存文件里带 `written_at` 和 `model`，纯为排查用；命中与否只看内容哈希

意义：任何一次晚期失败之后重跑，前面已经付过钱的调用一次都不用重发。CI 上由
`actions/cache` 挂 `.llm_cache/`，`restore-keys` 逐级回退，同一素材重跑直接命中。

请求体变一个字符（改 prompt、换图、调 temperature、换模型）键就变，所以缓存不需要
任何失效逻辑。

## 重试与错误分类

盲目重试会掩盖真实原因，所以先分类再决定重不重试。

| 情况 | 处理 |
| --- | --- |
| 连接失败、响应体为空或不是合法 JSON | 重试 |
| HTTP 429 / 500 / 502 / 503 / 504（及其余 5xx） | 重试 |
| HTTP 400 / 401 / 402 / 403（及其余 4xx） | **立即失败**，退 1，JSON 里带 `http_status` 和 `reason` |
| 客户端超时 | 最多重试 1 次 |

- 429 优先按响应头 `Retry-After` 等待，秒数和 HTTP 日期两种格式都能解析；
  没有该头才退回指数退避
- 指数退避带抖动，次数由 `--llm-max-retries` 配（默认 3），
  单次调用的**退避总时长上限 60s**（否则一个 `Retry-After: 3600` 就能挂满整个 job）
- **超时单独压低重试次数**：客户端超时不代表服务端没干活，很可能已经生成完并计了费，
  只是响应没回来。重试一次是止损，重试三次是重复付费三次。日志里会明确提示这一点。

翻译的多模型降级链（PR 之前就有）保留：可重试的失败耗尽后换下一个模型，
但不可重试的错误直接抛出 —— 密钥错了换几个模型都是一样的下场。

## 下载重试

CI run `30263087066` 挂在下载：archive.org 返 `HTTP Error 500`，退 3，人手重跑就过了。
无人值守时这就是白跑一趟。

- 外层重试默认 3 次（`--download-max-retries`），指数退避
  （`--download-backoff-sec`，基数默认 2s，单次上限 60s）
- 同时给 yt-dlp 带上它自己的 `--retries` / `--fragment-retries`，
  分片级抖动它自己就能吞掉，不必退到外层从头下
- 分类：5xx / 超时 / 连接重置**重试**；404、私有、已下架**立即退 3**；
  URL 格式非法**立即退 1**。认不出来的报错当抖动重试 —— yt-dlp 的报错面太广，
  多试两次的代价远小于丢掉一次本可以成功的无人值守跑

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
| `empty_transcript` | 转写结果为空 | — |

单段时长下限 `MIN_QUOTE_SEC = 15`：更短的片段先被筛掉，再去数条数。
总时长按**真正会拼进成片的那三段**算，不是全部候选之和 —— 拿候选池凑数没有意义。

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
