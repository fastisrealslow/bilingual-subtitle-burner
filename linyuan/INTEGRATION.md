# 与 bilingual-subtitle-burner 集成

把 linyuan-poc（监控找片）和 bilingual-subtitle-burner（出片）串成一条链：

```
监控发现 → 下载落盘 → 选片 → 出片（双语字幕+封面）→ 投稿素材包
linyuan-poc                bridge_produce.py      produce.py
```

## 现状

| 环节 | 状态 |
|------|------|
| 监控 + 下载 | ✅ 已有，472 个视频 10GB |
| 选片桥接 | ✅ `bridge_produce.py` |
| 出片 | ⚠️ **burner 侧需先支持中文源** |

## 阻塞点：produce.py 只支持英文片源

`produce.py` 是给英文访谈（芒格/巴菲特）设计的，两处写死了英文：

```python
# translate_windows()
zh_list = TR.translate_all(..., direction="en2zh")   # ← 写死英→中

# assemble()
cues = srt_filter(str(srt), start, end, srt_lang="en")   # ← 写死取英文轨
```

而林园视频**全部是中文源**，需要的是中→英（中文原文 + 英文翻译）。

好消息：底层模块本来就支持。`scripts/translate.py` 的 `--direction`
**默认值就是 `zh2en`**，`scripts/clip.py` 也有 `--srt-lang`。
只是 `produce.py` 这层没把参数透下去。

### 补丁（4 处）

**1. 加命令行参数**

```python
p.add_argument("--direction", default="en2zh", choices=["en2zh", "zh2en"],
               help="翻译方向。英文片源用 en2zh（默认），中文片源用 zh2en")
```

**2. `translate_windows()` 接受方向并正确落字段**

```python
def translate_windows(srt, quotes, work, translator, api_key, base_url,
                      direction="en2zh"):
    ...
    zh_list = TR.translate_all([e["text"] for e in picked], api_key,
                               model, base_url, direction=direction)
    ...
    for e, t in zip(picked, zh_list):
        if direction == "zh2en":
            # 源文是中文，译文是英文
            zh_text, en_text = e["text"], (t or "").strip()
        else:
            zh_text, en_text = (t or "").strip(), e["text"]
        bilingual.append({
            "index": e["index"], "start": e["start"], "end": e["end"],
            "zh": PR.normalize_cjk_punctuation(PR.to_simplified(zh_text)),
            "en": en_text,
        })
```

⚠️ 这里最容易写错：不能无脑把译文塞进 `zh` 字段。zh2en 时译文是**英文**，
塞进 `zh` 会让烧字幕时中英两行都是英文。

**3. `assemble()` 按源语言取字幕轨**

```python
def assemble(..., srt_lang="en"):
    ...
    cues = srt_filter(str(srt), start, end, srt_lang=srt_lang)
```

**4. `main()` 里把参数串起来**

```python
srt_lang = "zh" if args.direction == "zh2en" else "en"
bilingual = translate_windows(srt, quotes, ep_work, t, api_key, base_url,
                              args.direction)
segs, placements = assemble(video, srt, bilingual, quotes, ep_work,
                            ep_dir / name, args.sub_mode, args.sub_margin_v,
                            args.sub_avoid_gap, srt_lang=srt_lang)
```

**5.（可选）批量 schema 加字段**

`sources/*.json` 目前没有 `language` / `direction`，CI 跑中文源需要
在 `.github/scripts/plan_matrix.py` 里透传这两个字段。

## 为什么喂本地文件而不是 URL

`produce.py` 取源走裸 yt-dlp。我们最有价值的素材是 B站原片，而 B站对
数据中心 IP 直接 412 —— linyuan-poc 是绕 API 拿的流（`fetch_bilibili.py`
用 `view` + `playurl` 接口），produce.py 没有这条路。

视频已经在本地磁盘上，`--source` 支持本地路径，直接喂路径最稳。

## 选片策略

`bridge_produce.py` 按价值排序：

| 优先级 | 分类 | 理由 |
|--------|------|------|
| 1 | 股东大会现场 | 一手素材，二创的源头 |
| 2 | 演讲 | 完整长视频，够切 3 段金句 |
| 3 | 专访访谈 | 同上 |
| 4 | B站原片 | 已是她剪过的，价值次之 |
| — | 短切片 | **跳过**，produce.py 要挑 3 段拼 3 分钟，源太短会退 2 |

过滤条件：≥10 分钟、≥8MB。

出过的记进 `produced.json`，不重复烧钱。退 2（内容不达标）的也记下来 ——
那是刻意拒绝硬出，重试没有意义。

## 用法

```bash
# 看候选（不执行）
python3 bridge_produce.py

# 生成批量任务 JSON
python3 bridge_produce.py --emit-json /tmp/linyuan-jobs.json

# 实际出片（burner 侧打完补丁后）
python3 bridge_produce.py --run --limit 1 \
    --burner-dir ../bilingual-subtitle-burner
```

桥接会先自检 burner 是否支持中文源，不支持就拒绝执行 ——
跑下去会把中文当英文翻译，白烧 API 钱。

## 本地环境还缺的

| 项 | 状态 | 影响 |
|----|------|------|
| faster-whisper / PIL / requests | ✅ | — |
| ffmpeg | ✅ | — |
| **ffprobe** | ❌ | produce.py 的 `probe_duration` / `probe_size` 全靠它 |
| **SILICONFLOW_API_KEY** | ❌ | 不设置直接退 1 |

ffprobe 通常和 ffmpeg 同包，当前环境只装了 ffmpeg 单文件。
GitHub Actions 里 `apt-get install ffmpeg` 会带上，本地需要补。

## 成本

单条 3 分钟成片约 **¥0.03**（DeepSeek-V3 路径）。
按股东大会现场 6 条 + 演讲 11 条算，全出一遍约 ¥0.5。

`--no-vlm` 可以省掉封面 VLM 校验（第二大头），但封面可能选错人 ——
林园视频多是多人同框的访谈/股东大会，不建议关。
