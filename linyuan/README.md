# 林园监控 + 出片流水线

本目录是 [linyuan-poc] 并入 bilingual-subtitle-burner 的部分：
**监控林园全网内容 → 下载 → 出双语字幕短片 → 投稿 B站**，全自动。

与仓库根 `produce.py` 的关系
---------------------------
- 根 `produce.py`：英文访谈源（芒格/巴菲特），`direction=en2zh`
- 本目录 `produce_cn.py`：中文股东会/演讲源，原生 `zh2en`，另做人物参考照
  多帧核验、OCR 角标清理与复检、CPU 离线识别与时间对齐、ASR 专名术语表和
  loudnorm 响度标准化。下载后先执行独立素材闸门：至少120秒、原始短边480像素、
  多帧 OCR 源字幕和人物身份任一不合格，都不会进入 ASR/切片；失败报告由 FC
  消费并自动补调候选。裁切后的最终成片仍做分辨率和角标复检作为兜底。
- FC 投稿前做文件 SHA256、画面 dHash、Chromaprint 声纹和转写文本联合去重，
  并对相似观点设置 14 天冷却，避免跨平台同素材和标题变体反复发布。
- FC 只放行实际时长至少120秒、连续完整论述、人物、水印、最低480像素短边和指纹证明的 artifact；旧库存会被
  隔离并用原素材重新出片。某条被质量或重复闸门拦截后，同一投稿时段会换下一条。
- 投稿复用根的 `scripts/publish_bilibili.py`，不重复造轮子

目录内容
--------
```
监控        monitor_v2.py         8 源抓取引擎
            fetch_up_list.py      B站 UP 主全量清单（对标基准）
            *_seeds.json          抖音/好看/网易/第一财经种子池
            healthcheck.py        接口巡检（防静默失败）
            backtest.py           三项能力回测

下载        fetch_videos.py       站外视频（带过期自动刷新）
            fetch_bilibili.py     B站原片（view+playurl 绕 412）
            organize_videos.py    按场合归档

出片        produce_cn.py         中文源 → 3 分钟双语短片
            bridge_produce.py     选片 → 调 produce_cn.py

投稿        bili_login.py         扫码登录（生成凭据）
            bili_upload.py        biliup 投稿（默认定时发布）

可视化      server.py + console/  本地控制台
            console.sh            启停

一键        pipeline.sh           抓取→下载→归档→选片
            run_all.sh            + 立即下载（微博直链 1h 有效）
```

CI（仓库根 `.github/workflows/`）
--------------------------------
- `linyuan-monitor.yml`：每天 9 点抓取，提交元数据，视频不进仓库
- `linyuan-produce-cn.yml`：日常与手动生产共用main流程；读取版本化CPU离线配置，不调用ASR API，不使用large-v3回退。配置与实测见[ASR_OFFLINE.md](ASR_OFFLINE.md)。
- 日常成片以2～3分钟为主，每条为一个连续完整论述。每天发布6条，至少5条真人动态、最多1条audio_card。每条单独限时、隔离失败产物并保存已通过成片。

两条都用 `working-directory: linyuan`。secret 复用仓库已有的
`SILICONFLOW_API_KEY`；投稿要额外配 `BILIBILI_COOKIES`（默认不配=不投）。

快速开始
--------
```bash
cd linyuan
python3 -m pip install sherpa-onnx==1.13.7 pillow requests 'opencv-python<5' rapidocr-onnxruntime fonttools jieba
echo 'SILICONFLOW_API_KEY=sk-xxx' > .env   # 或用环境变量

python3 monitor_v2.py          # 抓取
python3 fetch_videos.py --all  # 下载
python3 bridge_produce.py      # 看选片
python3 produce_cn.py --source videos/xxx.mp4 --slug xxx --speaker 林园
```

人物核验默认使用第一财经官方节目封面作为林园参考照，只用于机器比对、不写入
成片。需要替换为自有参考图时设置 `LINYUAN_REFERENCE_URL`；视觉模型可用
`VISION_MODEL` 覆盖。

详细设计见同目录：SEEDS / TRACING / EARLY_ACCESS / HEADLESS / RISKS / DEPLOY。
