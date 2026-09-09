# 日常素材抓取与来源核验

入口是 `.github/workflows/linyuan-monitor.yml`（林园监控 · 每日抓取）。
北京时间每天 09:17、18:17 执行；修改抓取/溯源代码及手动触发也会执行。

同一任务顺序运行：
1. `source_gap_backfill.py` 核实指定参考、完整合集目录及来源对应关系。
2. `source_research.py` 查找参考账号的新视频，轮换检索未定位活动，限量下载并核验媒体。
3. `monitor_v2.py` 执行常规多渠道抓取。
4. 再合并来源证据，提交 SQLite、导出目录、核验结果和下次重试队列。

默认每轮最多 3 个检索词和 3 个媒体下载，每个下载最多 300 秒；
单个失败进入 6/12/24 小时退避，不中断其他来源，也不判定内容不合格。
同场的其他转载链接也进入队列，可在后续批次尝试。
超时会结束整个下载进程组，避免遗留 curl/ffmpeg 占用机器。

持续状态：
- `.automation/source_research_state.json`：查询位置、素材任务、尝试次数、下次重试时间。
- `.automation/source_research_report.json`：实际媒体核验、待处理数量、匹配候选。
- `.source-research-cache/`：通过音视频完整性核验后的音频指纹和画面，使用 Actions Cache 跨任务复用。
- `source-research-evidence-<run_id>` artifact：本轮可查看的截图、指纹和核验报告。

缓存丢失时重新进入限量队列；状态中的 inspected 只代表媒体文件核验完成，
不是合格成片，更不是官方原发已确认。音频匹配只产生 audio_match_candidate；
正式出片仍须通过生产工作流的 ASR、人物、时长、画面、字幕及去重门禁。
未知来源保留未确认标记。参考账号视频禁止直接进入生产。

`linyuan-source-gap-backfill.yml` 仅保留手动维护入口，日常流程不依赖另一个
workflow_run 是否触发，也不需要手动打开 probe_media。
