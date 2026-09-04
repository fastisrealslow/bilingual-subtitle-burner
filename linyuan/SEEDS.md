# 种子自动补充机制

## 为什么需要这个

抖音和好看视频的**搜索页有反爬**（抖音验证码 / 好看视频重定向首页），
但**单个视频播放页无防护**。所以抓取分两步：

```
第一步：发现视频链接（搜索）  →  第二步：解析 MP4 直链
```

第二步已在 `monitor_v2.py` 中完全自动化。本文档解决第一步。

## 为什么第一步不能写进 Python

在 Pod 内直连搜索引擎，全部验证失败：

| 方案 | 结果 |
|------|------|
| Bing `site:douyin.com` | 能返回结果，但中文结果不收录抖音/好看视频页 |
| 百度网页搜索 | 返回 227 字节风控页 |
| DuckDuckGo | 连接超时 |
| 好看视频站内搜索 | `/web/search/page` 空白页；`/videoui/page/searchresult` 重定向首页 |
| 好看视频相关推荐（滚雪球） | 命中率 0/7，推荐是泛化算法推荐，与主题无关 |
| 抖音搜索页 / 用户主页 / 话题页 | 验证码，或未登录显示首页推荐 |

**结论**：可用的搜索发现能力只存在于 OpenClaw 的 `web_search` 工具中，
Python 脚本无法调用它。因此发现环节必须由 agent 侧完成。

## 架构

```
┌─ agent / 子 agent ────────────┐   ┌─ Python（确定性逻辑）──────────┐
│  web_search 多轮搜索           │   │  seed_manager.py               │
│  ↓                             │   │   · 提取 ID / 归一化 URL        │
│  判断相关性，过滤同名无关内容    │──▶│   · 按 ID 去重                  │
│  （园林绿化 / 林园酒店 / 同名号）│   │   · 写入种子文件                │
└────────────────────────────────┘   └────────────┬───────────────────┘
                                                  │
                                     ┌────────────▼───────────────────┐
                                     │  monitor_v2.py                 │
                                     │   读种子 → 解析 MP4 → 存库      │
                                     │   → 导出 dashboard/data.json   │
                                     └────────────────────────────────┘
```

职责划分原则：**需要判断力的交给 agent，确定性的交给代码。**
相关性过滤必须由 agent 做——「林园」有大量同名干扰
（园林绿化、林园酒店美食、华林园景点、同名普通用户），
正则和关键词匹配无法可靠区分。

## 用法

### 查看种子池
```bash
cd /home/node/.openclaw/workspace/linyuan-poc
python3 seed_manager.py stats
```

### 手动添加
```bash
# 接受完整 URL 或纯数字 ID，自动归一化 + 去重
python3 seed_manager.py add-douyin "https://www.douyin.com/video/7545369238313635130"
python3 seed_manager.py add-haokan "11688231757041610344"
python3 seed_manager.py add-netease "VA141983Q"
python3 seed_manager.py add-yicai "https://www.yicai.com/video/103329354.html"

# 批量从 stdin
cat links.txt | python3 seed_manager.py add-douyin --stdin
```

### 让子 agent 自动补充

派一个子 agent，任务描述里必须包含三件事：
1. 指定搜索词（`site:douyin.com/video`、`site:haokan.baidu.com` 等）
2. **强调过滤同名无关内容**，并举例说明要排除什么
3. 给出 `seed_manager.py` 的调用方式

完整可复用的任务模板见 `seed_refresh_task.md`。

### 补完后抓取
```bash
python3 monitor_v2.py    # 自动读取新种子并解析
```

## 关于全自动化

如果要做成无人值守，用 cron 的**隔离子会话**模式
（`sessionTarget: isolated` + `payload.kind: agentTurn`），
在任务提示词里让子 agent 先补种子、再执行抓取。
这样定时任务内部就能调到 `web_search`。

目前定时任务按用户要求暂未创建。
