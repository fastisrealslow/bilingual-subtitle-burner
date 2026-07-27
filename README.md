# bilingual-subtitle-burner

将长视频（访谈/演讲）全自动处理为多条双语字幕短视频。

## 功能

- ASR 转写（Whisper medium，本地运行）
- 中英双语字幕生成（LLM 翻译）
- 金句自动识别与打分（过滤主持人段落）
- 自动切片 + 字幕烧录
- 封面自动选取（颜色规则识别主讲人，零 API 费用）
- B站上传素材包生成

## 一键出片

`produce.py` 把「一个视频源」一路跑到「一条 3 分钟成片 + 两张封面」，中间不需要人盯着。
下面这些是 `run.py` 分步流程之外的另一条入口，两者共用 `scripts/` 里的同一批模块。

```bash
python produce.py --source <URL 或本地路径> --slug <output-slug>
```

产物固定落在 `deliver/<slug>/`：

```
deliver/<slug>/
├── final.mp4          # 三段金句拼成的成片，双语字幕已烧进画面
├── cover_16x9.jpg     # B站封面
├── cover_9x16.jpg     # 抖音/竖版封面
└── meta.json          # 标题、seg 结构、时长、模型版本、commit、SHA256
```

### 本地用法

```bash
export SILICONFLOW_API_KEY=sk-xxxxxxxx

# 最简：一条 YouTube / archive.org 链接
python produce.py --source "https://www.youtube.com/watch?v=xxxx" --slug munger-2023

# 本地文件 + 手写标题 + 跳过封面的 VLM 校验（只按几何规则选帧，零 VLM 费用）
python produce.py --source ./raw/munger.mp4 --slug munger-2023 \
    --title-override "芒格谈耐心" --no-vlm

# 两个翻译都跑，额外出一张上下对比拼图 compare_grid.jpg
python produce.py --source ./raw/munger.mp4 --slug munger-dual --dual
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--translator` | `deepseek-v3`（默认）或 `claude-sonnet-4.6` |
| `--dual` | 两个翻译都跑，产出 `compare_grid.jpg` |
| `--no-vlm` | 封面跳过 VLM 校验，只按几何规则选帧 |
| `--cover-time-sec` | 手动钉死封面帧时间点（秒），跳过人脸预筛和候选采样。钉下的帧**仍要过 VLM 人物校验** |
| `--cover-allow-unverified` | 放行未经 VLM 人物核验的钉帧封面（默认关闭），打开后日志会打醒目告警 |
| `--strict-highlights` | 金句门槛忽略环境变量放宽，只认代码里的下限 |
| `--out` | 产物根目录，默认 `deliver/` |
| `--llm-cache-dir` / `--no-llm-cache` | LLM/VLM 响应缓存目录（默认仓库根 `.llm_cache/`）/ 关掉缓存全部实发 |
| `--llm-max-retries` | 单个 SiliconFlow 请求最多尝试几次（含首次，默认 `3`） |
| `--download-retries` / `--download-backoff-sec` | yt-dlp 下载的尝试次数（默认 `5`）与退避基数秒（默认 `10`） |
| `--download-socket-timeout` | yt-dlp 单个 socket 读写超时秒数（默认 `120`） |

**退出码**：`0` 成功 / `1` 配置错误 / `2` 内容质量不达标 / `3` 外部依赖失败。
退 2 表示这条片源挑不出够格的金句或封面 —— 是刻意拒绝硬出，重试没有意义。
SiliconFlow 的 400/401/402/403 归到退 1：改密钥或充值，自动重试没有用。
各阶段的拒绝原因见 [`docs/pipeline.md`](docs/pipeline.md)。

### 阶段顺序

开跑时整张表会打进日志（`[stage] 阶段顺序：…`），跑到哪一步一眼可见：

```
input → transcribe → highlight → cover-select → translate → assemble
                                       ▲
                                       └── 封面选帧 + 阈值判定，故意排在翻译之前
      → title → cover-render → manifest
```

`cover-select` 夹在 `highlight` 和 `translate` 中间不是随手排的：**翻译是整条流水线
的主要开销**，而封面挑不出合格帧是要退 2 的。把封面判定放到翻译后面，等于每条注定
被拒的片源都要先把翻译的钱花完才知道结果。所以先判封面，不达标就在花钱之前退出；
真正烧标题出图（`cover-render`）留到后面，那步不花 API 钱。

### LLM 缓存

所有 LLM/VLM 响应按内容寻址落盘到 `.llm_cache/`，键是
`sha256(模型名 + 端点 + 规范化请求体)`。规范化会对请求体做 `sort_keys`，键序抖动不会
造成缓存穿透；反过来 prompt、temperature 动一个字符键就会变。

这东西是为**晚期失败重跑**准备的：假设翻译跑完了，`assemble` 那步 ffmpeg 挂了，重跑
时前面金句、封面 VLM、翻译的调用全部命中缓存，一个请求都不会发出去，**已经付过的钱
不会再付第二遍**。命中时日志里是 `[llm-cache] 命中 …，不发请求`，收尾会打一行命中／
未命中统计。

只有成功响应进缓存。失败要是也缓存下来，重跑会永远卡在同一个错误上。转写的音频上传
（`files=` 多段上传）不参与缓存 —— 请求体是二进制流，内容寻址的成本高于收益。

缓存文件坏了（截断、不是合法 JSON、结构不对）不会炸掉流水线：会打一行
`[llm-cache] 缓存文件损坏（…）` 说明是哪个文件，然后回落去实发一次请求，成功后把那个
坏文件覆写掉。

`--no-llm-cache` 用在两个场合：想验证「改了 prompt 之后模型输出到底变没变」，以及怀疑
缓存本身有问题要排除它。日常重跑都不该带这个参数。

### 重试

SiliconFlow 的失败按状态码分类，不是一律重试：

| 状态 | 行为 |
| --- | --- |
| `429` `500` `502` `503` `504` | 指数退避重试，最多 `--llm-max-retries` 次（默认 3）。`429` 优先按响应头 `Retry-After` 等 |
| `400` `401` `402` `403` | **一次都不重试**，直接失败退 1 |
| 其他（`404` 等） | 不重试，退 3 |
| 连接失败 | 退避重试 |
| 客户端超时 | 退避重试，但**上限压到 2 次** |

盲目重试 400/401/402 只是把真实原因拖到几十秒超时之后才暴露：请求体不合法、密钥过期、
余额不足，重试多少次结果都一样，不如立刻把原因摆出来。

超时单独压到 2 次是因为**客户端超时不等于服务端没干活**：多半是服务端已经算完并且计了
费，只是响应没回来。再发一遍就是花两份钱买一份结果，所以这里比普通失败更保守。

退避是指数加抖动，单次上限 30s，一次请求所有退避加起来上限 60s —— 免得无人值守的定时
任务卡在一个持续 429 的端点上耗一整个 job 时长。

翻译那一路在重试之外还有一层模型降级：实测硅基流动的限流是**分模型**的，DeepSeek-V3
持续 429 的同一时刻 Qwen3-8B 完全正常，所以重试到上限仍不行就换下一个模型继续。

### 下载重试

yt-dlp 自己的 `--retries` / `--fragment-retries` 只覆盖单个 HTTP 请求和分片，整次调用
被 archive.org 的 500 顶回来时它直接就退了，所以外面还包了一层：默认最多 5 次
（`--download-retries`），退避基数 10s（`--download-backoff-sec`）逐次翻倍，单次上限 60s
—— 也就是 10s → 20s → 40s → 60s → 60s，另加不超过 25% 的抖动。

socket 超时用 `--socket-timeout` 显式钉到 **120s**（`--download-socket-timeout`），不用
yt-dlp 自带的 20s。实测同一个 archive.org 源连拉三次，首字节分别是 13.77s / 3.61s /
2.02s，速度只有 60~156 KB/s，一个 63.7 MB 的源光下载就要约 9 分钟 —— 20s 离 13.8s 只剩
6s 余量，源明明活着也会被判死（CI run 30274189811、30279507775 都是
`dn601208.us.archive.org` read timeout=20.0s，重试 3 次后退 3）。

源不存在或 URL 根本不受支持（`400` `401` `403` `404` `410`、`Unsupported URL`、
`Video unavailable`、私有视频）直接退 1，不重试 —— 重试也变不出一个不存在的视频。
`408` 和 `429` 不在这个名单里，那两个是「稍后再来」，值得重试。

### CI 用法

`.github/workflows/produce.yml` 两种触发方式：

1. **手动**：Actions 页面选「一键出片」，填 `source_url` + `slug` 即可
2. **批量**：往 `sources/` 里 push 一个 JSON，每条任务用 matrix 并发出片，
   schema 见 [`sources/README.md`](sources/README.md)

产物走 workflow Artifacts（`deliver-<slug>`，留存 90 天）。跑挂时会额外上传 `_tmp/`
里的转写、金句、译文，方便回查是哪一步不达标。

### Secrets 配置

仓库 Settings → Secrets and variables → Actions，只需要一项：

| Secret | 用途 |
| --- | --- |
| `SILICONFLOW_API_KEY` | 全部 LLM / VLM 调用（金句打分、翻译、标题、封面校验） |

`ANTHROPIC_API_KEY` **不要**配进 CI —— Claude 翻译路径保留着，但只在本地手动跑时用。

### 术语表（投资领域专有名词）

通用模型对投资圈的人名靠猜：实测 DeepSeek-V3 把
`NOW, THE OTHER HALF OF THAT QUESTION I LEAVE FOR MR. BUFFETT` 译成了
「这问题的另一半我留给**巴特勒**先生回答」—— Buffett 被音译成了「巴特勒」。

仓库根的 [`glossary.json`](glossary.json) 是对照表，翻译时整表注入系统提示词，
中→英、英→中两个方向都注入。

**加词**：往 `terms` 下任意一个分组里加一条 `"英文": "中文"` 即可 ——

```json
{
  "terms": {
    "people": {
      "Buffett": "巴菲特",
      "Klarman": "克拉曼"
    }
  }
}
```

分组名（`people` / `firms` / `concepts`）只为好读，注入时会拍平成一张表，
想加新分组直接加就行。渲染时长词排在前面，`Warren Buffett` 不会被 `Buffett`
截胡。

改表**会自动改变翻译提示词，进而改变 LLM 缓存键**，不会命中改词之前的旧译文
（`tests/test_translate_glossary.py` 锁死了这一点）。表读不到时只打告警、
按无术语表翻译，不会把整条流水线停掉。

### 语音识别听错人名（「巴特勒先生」的真正根因）

术语表上线后成片里仍然出现「巴特勒先生」。复查发现**根因在语音识别，不在翻译**：
faster-whisper 把那段音频听成了 `MR. BUTLER`，翻译只是忠实地把错的输入译对了。
术语表管的是「Buffett 该译成什么」，而原文里压根没有 Buffett 这个词。

**已经试过、实测无效的两条路，不要再走一遍：**

| 尝试 | 结果 |
| --- | --- |
| 换更大的 ASR 模型 | `base`→`Mr. Butler`、`small`→`Mr. Bob`、`medium`→`Mr. Bowman`，没有一个听对 |
| 给 whisper 加 `initial_prompt` / `hotwords` | 只影响开头，长音频走到 154 秒时已被前文上下文冲掉；且把分段变粗（76 段→39 段，最长段 4.5s→9.8s），而 `split_segment()` 对英文是原样返回的，会产生超长字幕 |

所以 `scripts/transcribe.py` 保持不动。**有效的修法在翻译侧**：在系统提示词里
紧跟对照表补一段说明，告诉模型原文来自语音识别、专名可能被听错，语境上明显该是
表里的词就按表输出。用真实生产 ASR 文本实测，仅术语表时 5/5 仍输出「巴特勒」，
补上说明后 5/5 修正为「巴菲特」。该视频全部 76 行真实 ASR 文本在两种提示词下
各翻一遍逐行 diff，涉及专名的差异只有 1 处（正是该修的那行），0 处破坏。

措辞对效果敏感 —— 收紧后实测 0/5 失效，见
`scripts/translate.py` 的 `ASR_NOTE_EN2ZH` / `ASR_NOTE_ZH2EN`，改动前请先复测。
说明本身依赖对照表，术语表为空时不注入。

**已知残留风险**：这是让模型「按语境把听着像表内人名的词纠回去」，如果视频里
真的出现一位与表内人名读音相近的无关人物（例如真有一位 Butler 先生），可能被
误纠正成「巴菲特」。频道做的是价值投资内容，这类同音无关人物远比听错巴菲特罕见，
所以这是有意接受的取舍；真撞上了就把该人名加进 `glossary.json` 消歧。

### 成本估算

单条 3 分钟成片（约 10 分钟片源）：

| 路径 | 每条约 |
| --- | --- |
| DeepSeek-V3（默认） | **¥0.03** |
| Claude Sonnet 4.6 | **¥0.42** |

转写在本地 / runner 上跑 faster-whisper `base`，不计费。封面 VLM 校验是第二大头，
`--no-vlm` 可以整块省掉。

## 快速开始

### 环境准备

```bash
pip install faster-whisper pillow requests
# ffmpeg 需要安装到 PATH 或 ~/.local/bin/
```

### 设置环境变量

```bash
export SILICONFLOW_API_KEY=your_api_key_here
export SILICONFLOW_MODEL=Qwen/Qwen2.5-72B-Instruct  # 可选，默认此值
```

### 运行

```bash
# 从本地视频文件开始（从 Step 2 跳过下载）
python3 run.py \
  --video /path/to/video.mp4 \
  --speaker 李录 \
  --speaker-desc "深蓝色西装" \
  --channel "价值投资讲堂"

# 从 B站 URL 开始（需要 cookie）
python3 run.py \
  --url "https://www.bilibili.com/video/BV1xx..." \
  --speaker 帕伯莱 \
  --cookies cookies.txt \
  --channel "价值投资讲堂"
```

### 断点续跑

```bash
# 从第 4 步（金句识别）开始重跑
python3 run.py --video video.mp4 --speaker 李录 --from-step 4
```

## 流程说明

| 步骤 | 功能 | 费用 |
|------|------|------|
| Step 1 | 下载视频（yt-dlp） | 免费 |
| Step 2 | ASR 转写（Whisper medium） | 免费（本地） |
| Step 3 | 中→英翻译 | ~¥0.03/30分钟视频 |
| Step 4 | 金句识别打分 | ~¥0.02/视频 |
| Step 5 | 文案生成 | ~¥0.01/视频 |
| Step 6 | 切片+字幕烧录（FFmpeg） | 免费 |
| Step 7 | 封面选取（颜色规则） | 免费（主讲人穿深色西装时） |
| Step 8 | 生成上传素材包 | 免费 |

**总计约 ¥0.06~0.10 / 30分钟视频**

## 封面选取逻辑

- 主讲人穿**深色（蓝/黑）西装** → 颜色规则自动识别，**零 API 费用，100% 准确**
- 主持人穿灰色西装 → 自动区分两人
- 识别失败时自动兜底（取片段中间帧）
- 可通过 `--speaker-desc "外貌描述"` 辅助识别

## 参数说明

```
--video         本地视频路径（与 --url 二选一）
--url           视频 URL（B站/YouTube，需对应 cookie 或代理）
--job           任务 ID（默认从文件名生成）
--speaker       主讲人姓名（用于金句识别和封面）
--speaker-desc  主讲人外貌描述（提高封面识别准确率）
--channel       频道名（用于文案生成）
--top-n         金句数量（默认 5）
--from-step     从第几步开始（断点续跑，1~8）
--to-step       跑到第几步结束
--force         强制重跑（忽略已完成状态）
--cookies       B站 cookie 文件路径
--proxy         下载代理
```

## 输出结构

```
output/jobs/{job_id}/
├── _raw.mp4          # 原始视频
├── full.srt          # 完整字幕
├── bilingual.json    # 双语字幕数据
├── highlights.json   # 金句片段
├── manifest.json     # 标题/文案/标签
├── state.json        # 各步骤完成状态
└── clips/
    ├── 01_标题.mp4
    ├── 01_cover.jpg
    └── ...
```

## 注意事项

- Whisper medium 模型需约 1.8GB 内存，large-v3 需 >4GB
- B站下载需登录 cookie（用浏览器插件导出 Netscape 格式）
- YouTube 在中国大陆需代理或本地下载后传入
- API Key 通过环境变量传入，**不要提交到代码库**
