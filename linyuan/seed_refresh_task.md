# 种子补充任务模板

派子 agent 时直接复制下面的内容作为 task。
将来挂 cron 全自动化时，也用这段作为 `payload.message`（末尾追加抓取步骤）。

---

你的任务：为「林园」（价值投资者、深圳林园投资管理有限公司创始人）监控项目补充视频种子链接。

工作目录：/home/node/.openclaw/workspace/linyuan-poc

步骤：

1. 用 web_search 工具做多轮搜索，目标是找到抖音和好看视频上关于「林园」投资相关的视频页链接。建议查询词（每个都搜一次，count 设 10）：
   - 林园 投资 site:douyin.com/video
   - 林园 茅台 片仔癀 site:douyin.com/video
   - 林园 演讲 访谈 site:haokan.baidu.com
   - 林园 股市 观点 site:haokan.baidu.com
   - 林园 价值投资 抖音 视频
   你也可以自行增加更多相关查询词。

2. 从搜索结果中提取：
   - 抖音视频链接：形如 https://www.douyin.com/video/7xxxxxxxxxxxxxxxxxx
   - 好看视频链接：形如 https://haokan.baidu.com/v?pd=wisenatural&vid=xxxxxxxxxxxxx

3. 严格过滤：只保留内容确实与「投资者林园」相关的（看标题/摘要判断）。必须排除同名无关内容，例如：园林绿化、林园酒店、林园餐厅美食、名叫林园的普通用户、华林园景点等。宁可少收也不要收错。

4. 用以下命令写入种子池（脚本会自动去重，重复的会跳过，不用担心重复提交）：
   cd /home/node/.openclaw/workspace/linyuan-poc
   python3 seed_manager.py add-douyin "链接1" "链接2" ...
   python3 seed_manager.py add-haokan "链接1" "链接2" ...

5. 最后运行 python3 seed_manager.py stats 确认结果。

请汇报：每个查询词找到多少条、过滤掉了哪些无关内容（举例说明）、最终新增了多少个种子、种子池总数变化。注意不要运行 monitor_v2.py，只负责补种子。

---

## 全自动化时的追加步骤

如果希望子 agent 补完种子后直接抓取，在上面末尾追加：

6. 补充完种子后，执行 `python3 monitor_v2.py`，等待完成后汇报本轮新增了多少条内容。

注意：`monitor_v2.py` 中 B站/微博/腾讯/雪球四个源依赖 CDP 浏览器
（127.0.0.1:18800）获取登录态；抖音和好看视频不依赖（好看视频连浏览器都不用）。
