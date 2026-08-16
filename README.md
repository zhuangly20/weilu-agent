# 围炉夜话 · AI团体心理支持智能体

清华大学人工智能创新大赛（2026）· 发展支持赛道 · 基于清小搭标准协议接入的自研智能体。

**一句话**：把苏轼、王阳明这样的"过来人"和与你背景相似的虚拟同龄人请进一个深夜围炉小组——带领者**小晴**（小清心的AI团体助手化身）+ 2位历史人物 + 2位同龄炉友，完成一场 8 轮结构化的团体活动，结束时生成《成长手记》与明信片。

**入组流程**：菜单（小晴自我介绍+活动方案列表+倾诉匹配入口）→ **相邀**（方案详情：玩法/时长/非治疗声明/明信片预告 + 四位炉友姓名与背景介绍 + 询问是否换人）→ **换人**（最多2位、同治疗角色槽位替补，旧成员道别+新成员入座，阵容写入进度标记跨轮延续）→ **开炉**（正式开始+破冰）→ 6轮正式流程。

**主题 × 形式矩阵**：主题（自我探索 / 学业压力 / 新生适应 / 就业迷茫）与形式（围炉夜话·文字 / 围炉画会·绘画共创）正交组合。画会形式中，成员与用户用文字轮流在同一幅画上添笔（笔触连着各自的心事），揭晓轮由 gpt-image-2 把全部笔触合成为真正的画作以附件送出（心晴谷验证过的加权拼接prompt），围绕画作完成"第一感受→笔触之问→笔触心声"的疗愈回顾。

## 核心设计

- **团体感**：每轮一条 assistant 消息模拟群聊剧本（`【角色名】发言`），守炉人 + 被点到成员轮换发言，关键轮全员亮相。**流式节奏器**（`app/pacer.py`）把增量重放成"逐字发言+发言间停顿"：观看时守炉人的话先逐字打出，停一拍，下一位成员再开口——协议限制下单条消息内能做到的最接近真实群聊的体验（实测全员亮相轮每位发言人间隔约3秒依次出现）。`WEILU_PACING=false` 可关闭。
- **治疗配额制**（沿用心晴谷验证过的结构）：2 过来人 + 1 不同视角 + 1 安静共鸣者；历史人物库含 `facts_boundaries` 防幻觉边界与 `safety_fallback` 兜底话术。
- **反谄媚约束**：守炉人只共情/点优势/推进，禁说教诊断；成员只讲自己，禁建议提问；代码级输出校验 + 重试。
- **状态无状态化**：协议没有用户ID，每轮回复末尾的程序化进度标记 `（围炉进度：第n/7轮 · xx｜主题：xx）` 既是进度条又是状态重建锚点，会话状态从消息历史完整恢复。
- **危机双通道**：正则两级检测——high 立即脚本化转介（不经LLM、零延迟），medium 注入共情指令由守炉人先接住情绪。
- **速度**：每轮仅 1 次 LLM 调用生成整轮群聊，SSE 流式透传；探测请求（max_tokens≤2）走快路径。
- **明信片**：第7轮在聊天里输出《成长手记》文字版的同时，代码解析手记要素，用 Pillow 渲染一张深夜炉火风明信片 PNG（霞鹜文楷字体），经 `x_soda.attachments` 返回文件卡片；`PUBLIC_BASE_URL` 环境变量决定附件下载地址，清小搭会自动转存。

## 目录

```
app/
├─ main.py       # FastAPI：/v1/models、/v1/chat/completions（SSE+非流式）、Bearer鉴权、/files/{token}
├─ protocol.py   # OpenAI兼容帧构造（role帧→content帧→stop帧+usage+attachments→[DONE]）
├─ director.py   # 编排：状态机推进、组队、历史压缩、计划执行、明信片附件
├─ session.py    # 进度标记解析、主题识别、按seed稳定组队
├─ prompts.py    # 整轮群聊生成prompt + 输出校验器
├─ llm.py        # 多供应商主备切换流式客户端
├─ safety.py     # 危机关键词检测与转介
├─ postcard.py   # 成长手记→明信片PNG渲染（Pillow）
├─ imagegen.py   # 画会文生图（/images/generations，失败降级为纯文字画会）
├─ pacer.py      # 流式节奏器（逐字发言+发言间停顿）
├─ files.py      # 附件临时存取（15分钟TTL）
└─ config.py     # YAML剧本加载 + .env供应商配置
assets/fonts/       # 霞鹜文楷（OFL开源协议）
config/
├─ characters.yaml  # 18位历史人物（迁移自心晴谷，公有领域）
├─ peers.yaml       # 8位虚拟同龄人（新写，高校背景）
├─ themes.yaml      # 4主题剧本 × 2形式（夜话/画会）
└─ crisis_keywords.yaml
scripts/trial.py    # 本地全流程试聊
tests/              # 40项测试：协议/状态机/换人/安全/明信片/节奏器/画会
deploy/             # nginx配置示例、上架信息、明信片示例、画会画作示例
```

## 本地开发

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest pytest-asyncio
.venv/Scripts/python -m pytest tests/ -q              # 全部测试
cp .env.example .env                                   # 填入模型key
.venv/Scripts/python -m uvicorn app.main:app --port 8200
.venv/Scripts/python scripts/trial.py academic 8       # 真模型全流程试聊
```

## 部署（与史记同服务器共存）

1. 上传本目录到服务器（如 `/opt/weilu-agent`），`cp .env.example .env` 填好密钥。
2. `docker compose up -d --build`（容器只监听 127.0.0.1:8200，不直接暴露公网）。
3. Nginx：参照 `deploy/nginx-weilu.conf.example`——新子域名或现有域名加 `location /weilu/` 均可（SSE 必须关缓冲、超时≥120s）。
4. 自测（对照接入指南§8）：

```bash
BASE="https://你的域名/weilu/v1"; KEY="你的WEILU_API_KEY"
curl -i "$BASE/models" -H "Authorization: Bearer $KEY"
curl -s -X POST "$BASE/chat/completions" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}]}'
curl -N -X POST "$BASE/chat/completions" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"stream":true,"max_tokens":1,"messages":[{"role":"user","content":"你好"}]}'
```

5. 清小搭「标准协议接入」向导：API地址填 `https://域名/weilu/v1`，密钥填 `WEILU_API_KEY`，走完探测→试聊→完善信息→审核上线。

## 迭代计划（评审期 8.20-9.6）

1. 绘画共创轮（文生图 + 图片附件）
2. LLM 智能选人（当前为按会话seed稳定选人）
3. 会话数据统计与发言质量监控面板
