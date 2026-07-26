# 首次运行 Checklist（Actions 手动触发一条帕伯莱 9:49 短访谈）

目标：跑通"下载→转写→翻译→切片→封面→打包"的端到端链路,产出竖屏双语成片存进 Artifacts,**人工审过再谈上传**。

---

## 1. 只需先配一个 Secret

进入仓库 → **Settings → Secrets and variables → Actions → New repository secret**:

| 名称 | 值 | 是否必需 |
|------|----|--------|
| `SILICONFLOW_API_KEY` | 你的硅基流动 API Key | ✅ **必需**(翻译/金句/文案/封面 VLM 全部依赖) |
| `YOUTUBE_COOKIES` | YouTube Netscape cookie 全文 | ⚠️ 建议(帕伯莱这条不加大概率也能下,加了更稳) |
| `BILI_COOKIES_JSON` | biliup 登录产物 | ❌ 首次不需要(不上传) |
| `DOUYIN_COOKIES_JSON` | Playwright storage_state | ❌ 首次不需要(不上传) |

> `SILICONFLOW_API_KEY` 领取地址:https://siliconflow.cn/ → API 密钥 → 新建

---

## 2. 手动触发运行

到仓库 [Actions 页面](https://github.com/fastisrealslow/bilingual-subtitle-burner/actions) → 左侧点 **"双语投资短视频流水线"** → 右上 **Run workflow** → 按下面填:

```
Branch:            main
url:               https://www.youtube.com/watch?v=FbrM6ozHOG0
speaker:           帕伯莱
channel:           价值投资讲堂
language:          en
direction:         en2zh
top_n:             5
whisper_model:     small
do_upload_bili:    ❌ 不勾
do_upload_douyin:  ❌ 不勾
```

点 **Run workflow**。

---

## 3. 预期时间线(9:49 视频)

| 阶段 | 预计耗时 | 说明 |
|------|--------|-----|
| 装依赖(ffmpeg/whisper/biliup) | 2-3 min | 首次跑无缓存较慢 |
| Step 1 yt-dlp 下载 | 30-60s | 有 `YOUTUBE_COOKIES` 更稳 |
| Step 2 Whisper `small` 转写 | 3-8 min | **CPU 大头**,占总时间 60% |
| Step 3 翻译(en→zh) | 1-2 min | SiliconFlow 并发 |
| Step 4 金句评分 | 30-60s | LLM 打分 |
| Step 5 标题/文案 | 1-2 min | LLM 并发 |
| Step 6 ffmpeg 切片+竖屏 | 1-2 min | 5 段短片 |
| Step 7 封面(颜色规则+VLM) | 30-60s | 大多会走 VLM 选帧 |
| Step 8 打包(不上传) | <10s | 只生成素材包 |
| **总计** | **约 12-20 min** | |

---

## 4. 产物在哪拿

Actions run 页面底部 **Artifacts → `pipeline-output-<run_id>`**,下载解压后:

```
output/jobs/<job_id>/
├── clips/
│   ├── 01_<金句标题>.mp4    ← 竖屏双语成片(1080×1920)
│   ├── 01_cover.jpg          ← 封面
│   ├── 02_...
│   └── 05_...
├── package/                  ← B站上传素材包(每条一个子目录)
│   ├── 01_<safe_title>/
│   │   ├── video.mp4
│   │   ├── cover.jpg
│   │   ├── info.json         ← 标题/简介/标签/分区建议
│   │   └── biliup.toml       ← 可直接用的 biliup 配置
│   └── ...
├── upload_list.md            ← 人类可读清单(建议先看这个)
└── upload_list.json
```

---

## 5. 人工验收清单(务必逐项过一遍)

打开 5 条 `01_*.mp4` ~ `05_*.mp4`,检查:

- [ ] **竖屏尺寸对**:1080×1920,原视频居中,上下有模糊背景填充
- [ ] **双语字幕都在**:英文小字在顶部,中文大字在底部,不遮挡人脸
- [ ] **字幕时间对齐**:说到哪里字幕跟到哪里,不早不晚
- [ ] **中文翻译通顺**:没有明显英文残留、术语错译("moat" 应该是"护城河"这种)
- [ ] **金句选段有代表性**:不是随机中间截一段,而是"卖出时机/周期股/护城河"这类完整观点
- [ ] **封面美观**:主体清晰,不是全黑/全糊/嘴巴张大的怪帧
- [ ] **标题吸引人**:看 `upload_list.md` 里 5 条标题,能不能让你想点开

---

## 6. 常见问题应对

| 症状 | 原因 | 解决 |
|------|-----|------|
| Step 1 报 `HTTP Error 403` 或 `Sign in to confirm` | YouTube 反爬 | 加 `YOUTUBE_COOKIES` Secret,或换个更冷门的备选 URL |
| Step 2 转写超时 | Whisper `small` 在 CPU 上仍很慢 | 改用 `tiny` / 或改本地 Mac 跑第一次 |
| Step 3 报 `SILICONFLOW_API_KEY` 未设置 | Secret 没配 | 回第 1 步 |
| 中文字幕出现英文残句 | 翻译片段丢失 | 检查 `output/.../translate.zh.srt`,可能是硅基流动限流 |
| 封面全是黑帧 | 视频开头有黑场 | 已内置颜色规则跳过,若仍差则调 `speaker_color=auto` |

---

## 7. 验收通过后,才开启真正上传

看完 5 条觉得满意再做:

1. 加 `BILI_COOKIES_JSON` Secret(本地 `biliup login` 生成 `cookies.json` 全文粘进去)
2. 同一条 URL 再手动触发一次,这次**勾上 `do_upload_bili`**
3. 系统会自动用 `copyright=2` 转载模式上传,来源填原 YouTube URL
4. 到 B站 [创作中心](https://member.bilibili.com/platform/upload-manager/article) 查稿件状态

抖音那步等 B站稳定跑通几条之后再单独试。

---

**准备好了就去 Actions 页面点 Run workflow。跑完把 Artifacts 里的 `upload_list.md` 或任意一条 mp4 发我,我帮你审。**
