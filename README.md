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
| `--strict-highlights` | 金句门槛忽略环境变量放宽，只认代码里的下限 |
| `--out` | 产物根目录，默认 `deliver/` |

**退出码**：`0` 成功 / `1` 配置错误 / `2` 内容质量不达标 / `3` 外部依赖失败。
退 2 表示这条片源挑不出够格的金句或封面 —— 是刻意拒绝硬出，重试没有意义。
各阶段的拒绝原因见 [`docs/pipeline.md`](docs/pipeline.md)。

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
