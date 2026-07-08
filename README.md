# bilingual-subtitle-burner

将长视频（访谈/演讲）全自动处理为多条双语字幕短视频。

## 功能

- ASR 转写（Whisper medium，本地运行）
- 中英双语字幕生成（LLM 翻译）
- 金句自动识别与打分（过滤主持人段落）
- 自动切片 + 字幕烧录
- 封面自动选取（颜色规则识别主讲人，零 API 费用）
- B站上传素材包生成

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
