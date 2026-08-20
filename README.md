# 清心圆桌 · AI团体支持智能体

清华大学人工智能创新大赛（2026）· 发展支持赛道 · 基于清小搭标准协议接入的自研智能体。

**一句话**：一间洒满阳光的暖房，一张永远留着空座的圆桌。带领者**小晴**（小清心的AI团体助手化身）按用户的话题匹配一桌刚刚好的 AI 同伴，在 20 分钟左右的结构化团体里把一件事聊透，结束时生成《圆桌留笺》与明信片。

## 六个节目（三类形态，一套服务）

**深度团体**（group_v2 引擎，主题参数化——四个主题共用一台团体过程状态机，各带专属团友与活动配置）

| 节目 | 主题 | 专属团友（虚构AI角色） | 四个活动 |
| --- | --- | --- | --- |
| 1️⃣ 减压安心之旅 | academic | 林之衡 / 许南枝 / 陈默 | 入桌与认识 → 四个故事的压力圆桌 → 互助讨论与减压共创 → 收获与告别 |
| 2️⃣ 新生适应 | connection | 顾一帆 / 沈知夏 / 林之衡 | 入桌与认识 → 新环境的瞬间圆桌 → 微连接共创 → 收获与告别 |
| 3️⃣ 爱情探索 | love | 程亦川 / 陆嘉树 / 温言 | 入桌与认识 → 心动圆桌 → 三元棱镜与亲密共创（斯滕伯格爱情三元论）→ 收获与告别 |
| 4️⃣ 就业迷茫 | career | 姜遥 / 方叙 | 入桌与认识 → 路口的故事圆桌 → 与不确定共处共创 → 收获与告别 |

**轻团体**（painting_studio 引擎，约10分钟：小晴问一次、每人对一次）

| 节目 | 流程 |
| --- | --- |
| 5️⃣ 圆桌画室 | 团友A落笔 → 用户落笔 → 两位团友各添一笔 → 四笔合成一幅真正的画（文生图，失败降级为文字画）→ 画作揭晓片刻即席感受（唯一可多聊两句的时刻，小晴立即收住）→ 反思 → HTML明信片附件 |

**对话面板**（panel 引擎，史记人物库）

| 节目 | 流程 |
| --- | --- |
| 6️⃣ 时空对话 | 按话题匹配四位史记人物（或用户点名/选预设主题）→ 用户问一个问题 → 四位依次作答 → 一轮隔世辩论 → 可追问、换题、告别 |

## 核心设计

- **三形态·一服务**：三个引擎共享一个 FastAPI 服务、一套安全层、一个 OpenAI 兼容接口；菜单按数字/活动名/倾诉关键词路由，主题匹配用长度加权关键词打分（平手按 主题顺序：connection → love → academic → career → self）。
- **规则按活动隔离**：每次 LLM 调用只带当前活动、当前主题、当前阶段的规则与团友档案——爱情场不会看到减压场的配置；时空对话只嵌入被选中 4 位人物的 skill 全文（101 位库源自 shijimaster，已剔除非历史角色）。
- **状态无状态化**：协议没有用户ID，状态藏在每条回复末尾的 HTML 注释标记里（`QXG2|…|theme=`、`QXSD|…`、`QXPA|…`），每轮从消息历史重建；服务重启不丢场，部署无需数据库。
- **团体感**：每轮一条 assistant 消息模拟群聊剧本（`【角色名】发言`），流式节奏器重放出"逐字发言+发言间停顿"的群聊体验；`WEILU_PACING=false` 可关。
- **输出治理**：所有公开文本过统一禁词表（围炉/炉火/夜话等旧意象）+ 各引擎结构校验（说话人白名单、阶段名在场、笔触格式、辩论交叉引用等），失败一次重试、再失败落确定性兜底，绝不卡流程。
- **反谄媚约束**：带领者只共情/推进，禁说教诊断；团友只讲自己，禁越界建议；爱情主题绝不替真人下三元论结论，就业主题不提供行业/薪酬信息。
- **危机双通道**：正则两级检测——high 立即脚本化转介（不经LLM、零延迟），medium 注入共情指令；三条通道各自保留原活动状态标记，危机过后能回到原场。

## 目录

```
app/
├─ main.py            # FastAPI：/v1/models、/v1/chat/completions（SSE+非流式）、Bearer鉴权、/files/{token}
├─ protocol.py        # OpenAI兼容帧构造
├─ director.py        # 编排：路由分发、计划执行、校验重试、附件
├─ group_v2.py        # 深度团体引擎（主题参数化四活动状态机）
├─ painting_studio.py # 轻团体·圆桌画室引擎
├─ panel.py           # 对话面板·时空对话引擎（史记人物）
├─ postcard_html.py   # 画室明信片HTML渲染（无LLM、纯确定性）
├─ session.py         # 菜单/主题识别/入口路由、标记解析
├─ prompts.py         # 公共prompt组件、禁词与输出校验器
├─ llm.py             # 多供应商主备切换客户端
├─ safety.py          # 危机关键词检测与转介
├─ postcard.py        # 深度团体成长手记→明信片PNG（Pillow）
├─ imagegen.py        # 画室文生图（失败降级为文字画）
├─ pacer.py           # 流式节奏器
├─ files.py           # 附件临时存取（15分钟TTL，HTML报告24h）
└─ config.py          # YAML加载 + 主题配置 + 史记人物库
config/
├─ group_v2.yaml         # 减压安心之旅（academic）
├─ group_connection.yaml # 新生适应
├─ group_love.yaml       # 爱情探索（斯滕伯格三元论·三元棱镜）
├─ group_career.yaml     # 就业迷茫
├─ shiji_registry.json   # 史记人物库（106→101位，迁移自shijimaster）
├─ characters.yaml / peers.yaml / themes.yaml / crisis_keywords.yaml
tests/               # 111项测试：三形态状态机/校验/兜底/明信片/安全/路由
scripts/acceptance_programs.py  # 本地真实链路验收（六节目全流程）
deploy/              # nginx配置示例、上架信息、预览HTML、logo
```

## 本地开发

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest pytest-asyncio
.venv/Scripts/python -m pytest tests/ -q              # 111项全部测试
cp .env.example .env                                   # 填入模型key
.venv/Scripts/python -m uvicorn app.main:app --port 8200
.venv/Scripts/python scripts/acceptance_programs.py    # 六节目真实链路验收（先起服务，默认8202端口）
```

## 部署（与史记同服务器共存）

推荐用 `deploy/setup-on-server.sh` 一键完成（克隆→.env检查→容器→Nginx→certbot→自测，末尾打印 baseUrl 与 API Key）：

```bash
bash deploy/setup-on-server.sh git@github.com:zhuangly20/weilu-agent.git weilu.你的域名.com
```

手动步骤等价于：

1. 克隆仓库到服务器（如 `/opt/weilu-agent`），上传本地 `.env` 到该目录（含模型key，不入仓库）。
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

5. 清小搭「标准协议接入」向导：API地址填 `https://域名/weilu/v1`，密钥填 `WEILU_API_KEY`，走完探测→试聊→完善信息→审核上线。上架文案直接取 `deploy/清小搭上架信息.md`。
