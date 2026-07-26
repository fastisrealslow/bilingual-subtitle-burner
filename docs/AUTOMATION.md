# 全自动流水线部署指南（GitHub Actions + B站/抖音上传）

把 YouTube 上知名投资人（李录、帕伯莱等）的英文视频，自动转成中文双语短视频，
定时搬运到 **B站** 和 **抖音**。整条链路跑在 GitHub Actions 上，**免费、定时、全自动**。

---

## 一、整体架构

```
YouTube 视频 URL
   │
   ▼  GitHub Actions（免费、定时 cron / 手动触发）
┌─────────────────────────────────────────────────────────┐
│ 1 fetch     yt-dlp 下载（带 cookie 突破数据中心 IP 反爬）  │
│ 2 transcribe Whisper 转写英文（CPU，建议 small/medium）    │
│ 3 translate  SiliconFlow 硅基流动 en→zh 翻译              │
│ 4 highlight  LLM 金句打分选段                             │
│ 5 copywrite  生成标题/简介/标签                          │
│ 6 clip       ffmpeg 切片 + 双语 ASS 字幕 + 竖屏 9:16      │
│ 7 cover      封面（颜色规则 + VLM 选帧）                  │
│ 8 upload     biliup 自动上传 B站  ✅                       │
│ 9 douyin     Playwright 自动上传抖音（可选，风控风险高）⚠ │
└─────────────────────────────────────────────────────────┘
   │
   ▼  产物存 Artifacts（成片、清单、上传结果）
```

**关键取舍**：
- **B站**：`biliup` 纯命令行上传，cookie 放 Secrets，CI 内稳定可靠 ✅
- **抖音**：无官方 API，只能用 Playwright 模拟网页上传。**数据中心 IP（GitHub Actions）
  极易触发风控 / 验证码 / 登录态失效**。已按你的要求硬塞进 Actions，但成功率不保证；
  若频繁失败，建议把抖音这一步挪到你 Mac / 家用 IP 的机器上跑（脚本通用）。

---

## 二、需要配置的 GitHub Secrets

进入仓库 → **Settings → Secrets and variables → Actions → New repository secret**，
逐个添加下面的 Secret：

| Secret 名称            | 必填 | 说明 |
|------------------------|------|------|
| `SILICONFLOW_API_KEY`  | ✅    | 硅基流动 API Key（翻译/金句/文案/封面 VLM 都要用）。无此 key 第 3-5 步会失败 |
| `YOUTUBE_COOKIES`      | 建议 | YouTube cookies（Netscape 格式全文）。突破数据中心 IP 反爬，很多视频不带会下载失败 |
| `BILI_COOKIES_JSON`    | 上B站必填 | B站登录 cookie（`biliup login` 生成的 `cookies.json` 全文） |
| `DOUYIN_COOKIES_JSON`  | 上抖音必填 | 抖音 cookie（Playwright storage_state JSON 或 cookie 列表全文） |

> 所有 cookie 都由工作流写进 `secrets/` 目录（已 `.gitignore`，绝不入库），运行结束即销毁。

### 2.1 如何拿到 `SILICONFLOW_API_KEY`
登录 https://siliconflow.cn → API 密钥 → 新建 → 复制。免费模型 `Qwen/Qwen3-8B` 够用，
付费兜底可切 `deepseek-ai/DeepSeek-V3`（改工作流 `SILICONFLOW_MODEL` 即可）。

### 2.2 如何拿到 `YOUTUBE_COOKIES`
浏览器装 “Get cookies.txt LOCALLY” 扩展 → 登录 YouTube → 导出 `youtube.com` 的
Netscape 格式 cookies → 把**文件全文**粘进 Secret。

### 2.3 如何拿到 `BILI_COOKIES_JSON`
本地装 biliup 并扫码登录一次：
```bash
pip install biliup
biliup login          # 弹出二维码，用 B站 App 扫码
# 成功后当前目录生成 cookies.json
cat cookies.json      # 复制全文，粘进 BILI_COOKIES_JSON
```
（也可用 biliup-rs 二进制，产物同样是 cookies.json。）

### 2.4 如何拿到 `DOUYIN_COOKIES_JSON`
抖音没有命令行登录，需要本地跑一次有头浏览器导出登录态：
```bash
pip install playwright && playwright install chromium
python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    ctx = b.new_context()
    pg = ctx.new_page()
    pg.goto("https://creator.douyin.com")
    input("在打开的浏览器里扫码登录抖音创作者后，回到这里按回车...")
    ctx.storage_state(path="douyin_cookies.json")
    b.close()
print("已保存 douyin_cookies.json")
PY
cat douyin_cookies.json   # 复制全文，粘进 DOUYIN_COOKIES_JSON
```
> 抖音登录态时效短，失效后需重新导出。这是抖音在 CI 里最脆弱的一环。

---

## 三、两种运行方式

### 方式 A：手动触发（推荐先用这个测通）
Actions 页面 → 选 “双语投资短视频流水线” → **Run workflow** → 填参数：
- `url`：YouTube 视频链接（必填）
- `speaker`：李录 / 帕伯莱 …
- `language`：`auto`（自动识别，英文视频会识别成 en）
- `direction`：`en2zh`（英译中）
- `whisper_model`：`small`（CPU 快）或 `medium`（更准更慢）
- `do_upload_bili`：勾上才真正传 B站；不勾只出成片存 Artifacts
- `do_upload_douyin`：勾上才尝试传抖音

### 方式 B：定时批量（cron）
1. 编辑仓库根目录 `queue.txt`，每行放一个 YouTube URL（`#` 注释）。
2. 到点后（默认 UTC 22:00 = 北京时间次日 06:00）自动逐条处理，
   处理完的 URL 会自动从 `queue.txt` 移除并记入 `queue_done.txt`。
3. 改时间：编辑 `.github/workflows/pipeline.yml` 里的 `cron`。
   - 例：北京时间每天 08:00 → UTC 00:00 → `cron: "0 0 * * *"`

> 定时任务默认开启 B站上传。抖音默认不在定时里跑（风控太不稳定），
> 需要的话在定时 run 段里加 `--douyin --do-douyin-upload`。

---

## 四、产物在哪看
每次运行结束，Actions run 页面底部 **Artifacts** 里可下载：
- `*.mp4`：竖屏双语成片
- `upload_list.md` / `upload_list.json`：每条短片的标题/简介/标签/分区
- `upload_result.json`：B站上传成功/失败明细
- `douyin_result.json`：抖音上传明细（若跑了）

---

## 五、成本与限制（务必知晓）

| 项目 | 说明 |
|------|------|
| **GitHub Actions 免费额度** | 公开仓库 Actions 完全免费；私有仓库每月 2000 分钟。Whisper 转写是耗时大头 |
| **Whisper CPU 速度** | Actions 无 GPU，`small` 约实时的 2-4 倍耗时；长视频建议先切短或用 `small` |
| **YouTube 反爬** | 数据中心 IP 常被限制，务必配 `YOUTUBE_COOKIES`；仍可能偶发失败 |
| **抖音风控** | 数据中心 IP + 无法扫码 → 极易失败/触发验证码。失败请改在本地/家用 IP 跑 step9 |
| **B站转载规范** | 搬运国外视频请用 `copyright=2`（转载）并注明来源，避免违规 |
| **版权** | 搬运他人视频存在版权风险，请自行确认授权/合理使用边界 |

---

## 六、本地跑（等价命令）
CI 里其实就是调用 `run.py`。本地同样可跑：
```bash
# 只出成片、不上传
python run.py --url "https://youtu.be/XXXX" --speaker 帕伯莱 \
  --language auto --direction en2zh --vertical --whisper-model small

# 出成片 + 自动上传 B站
python run.py --url "https://youtu.be/XXXX" --speaker 帕伯莱 \
  --language auto --direction en2zh --vertical --whisper-model small \
  --do-upload --bili-cookies cookies.json --copyright 2 --source "https://youtu.be/XXXX"

# 追加抖音上传（本地/家用 IP 更稳）
python run.py --url "https://youtu.be/XXXX" --speaker 帕伯莱 \
  --language auto --direction en2zh --vertical --whisper-model small \
  --douyin --do-douyin-upload --douyin-cookies douyin_cookies.json
```
