# GitHub Actions 部署指南

## 可行性结论

**可以跑，但视频不能进仓库。**

| 项目 | 状态 |
|------|------|
| 大模型依赖 | ✅ 无。抓取全链路是纯规则（正则/JSON解析/SQLite），零 LLM 调用 |
| 浏览器依赖 | ⚠️ 6/7 源可无浏览器运行，仅雪球需要（自动降级跳过）|
| 运行耗时 | ✅ 单轮抓取约 45 秒，远低于 Actions 限额 |
| 免费额度 | ✅ 每天 2 次 × 2 分钟 ≈ 120 分钟/月（免费 2000 分钟）|
| 视频存储 | ❌ 仓库软限制 1GB、单文件 100MB；当前视频库 9GB、最大单文件 573MB |

## 各源在无浏览器环境的实测表现

| 源 | 条数 | 耗时 | 可用 |
|----|------|------|------|
| shareholder_meeting | 135 | 8.4s | ✅ |
| weibo_search | 35 | 3.4s | ✅ |
| haokan_video | 19 | 3.6s | ✅ |
| bilibili_search | 19 | 0.6s | ✅ |
| netease_video | 17 | 22.1s | ✅ |
| tencent_live | 8 | 6.9s | ✅ |
| douyin_video | 种子驱动 | — | ✅ |
| xueqiu_search | — | — | ❌ WAF 需执行 JS |

## 关键设计约束

### 1. 抓取与下载必须在同一 Job

微博 CDN 直链带 `Expires`，**实测仅 1 小时有效**。若拆成两个 Job 或两次 workflow，
下载时必然 403。workflow 中两步紧邻执行。

### 2. 视频走 Artifacts，不进仓库

```yaml
- uses: actions/upload-artifact@v4
  with:
    name: videos-${{ github.run_number }}
    path: videos/
    retention-days: 30
```

`.gitignore` 已排除 `videos/`、`*.mp4`。仓库只提交元数据
（`dashboard/data.json`、种子文件、`up_videos.json`、`monitor_v2.db`）。

若需长期保存视频，建议接对象存储（OSS/S3），在 workflow 里加一步上传。

### 3. 系统依赖

```yaml
sudo apt-get install -y poppler-utils ffmpeg
```

- `pdftotext`（poppler-utils）：解析股东大会公告 PDF 提取会议时间地点
- `ffmpeg`：合并 B站 DASH 音视频流

### 4. 内部信息隔离

**不得进入公开仓库**的内容（已在 `.gitignore` 中排除）：

- 内部部署平台的配置文件与发布产物
- 本机/局域网 IP、内部域名、任何凭据文件（`.env`、`cookies.json`）

原则：仓库里只允许出现公开服务的 API 端点（B站/微博/硅基流动等）和
`127.0.0.1` 这类本机约定地址。`CDP_URL` 读环境变量，默认值仅本地生效。

## 部署步骤

1. 新建 GitHub 仓库（**强烈建议私有**：监控数据属内部资产，且 Actions
   日志会带源站响应细节）
2. 复制以下文件：
   ```
   monitor_v2.py  monitor_v2_config.json
   fetch_videos.py  fetch_bilibili.py  fetch_up_list.py
   seed_manager.py  organize_videos.py  check_links.py  backtest.py
   *_seeds.json  up_videos.json
   .github/workflows/monitor.yml  .gitignore
   ```
3. 首次运行前，仓库设置 → Actions → General → Workflow permissions
   勾选 **Read and write permissions**（脚本需要 commit 数据变更）
4. 手动触发一次 `workflow_dispatch` 验证

## 与本地方案的差异

| 能力 | 本地（有浏览器） | GitHub Actions |
|------|----------------|----------------|
| 抓取源 | 8 个 | 7 个（无雪球）|
| 视频落盘 | 永久保存 | Artifacts 保留 30 天 |
| 种子自动补充 | 可调 web_search | ❌ 需人工或外部触发 |
| Dashboard 部署 | 本地 HTTP 服务 | 需另配（GitHub Pages 等）|

**种子补充是最大缺口**：抖音/好看视频靠种子池驱动，而发现新种子需要
搜索引擎能力，Actions 环境内无法调用。建议保持「本地补种子 → 提交种子文件 →
Actions 自动抓取」的混合模式。
