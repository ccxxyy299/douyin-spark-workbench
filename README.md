# 个人工作台 · 抖音续火花

一个本地运行的个人工具集。当前第一个模块是**抖音续火花**：定时给抖音好友发私信
（文字 / 图片 / 抖音原生表情），维持聊天火花不断。

工作台内核只做三件事 —— 模块注册、导航、通用布局；业务全部在模块里，
以后加「定时发布」「数据看板」这类功能是加模块，不是改内核。

---

## 它长什么样

```
工作台概览        今日任务 / 已发送 / 好友数 / 已勾选 / 有火花的
├── 账号          多个抖音号，每个一个独立浏览器 profile
├── 好友与火花    扫描私信页 → 好友列表 + 火花天数 → 勾选
├── 文案与表情    文字模板 / 图片 / 采集抖音表情面板
├── 发送计划      发送时间 + 随机抖动 + 目标好友 + 文案来源
└── 任务记录      状态、错误、失败截图、日志
```

---

## 快速开始

### 1. 后端

```powershell
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. 前端

```powershell
cd web
npm install
npm run dev          # http://localhost:5173
```

开发时 Vite 会把 `/api` 代理到 `http://127.0.0.1:8766`，所以后端也要起来：

```powershell
cd server
python -m uvicorn app.main:app --port 8766
```

### 3. 生产模式（单进程，前端由后端托管）

```powershell
cd web
npm run build        # 产物在 web/dist
cd ..\server
python -m uvicorn app.main:app --port 8766
# 打开 http://127.0.0.1:8766
```

### 4. 第一次使用

1. **账号** 页 → 新建账号 → 点「扫码登录」→ 浏览器弹出来，手机抖音扫码。
   登录态存进 `server/data/profiles/douyin-<id>/`，之后长期有效。
2. **好友与火花** 页 → 点「刷新好友列表」→ 扫出好友列表和各自的火花天数 →
   勾选要续火花的人。
3. **文案与表情** 页 → 建几条文字模板（可以点「采集抖音表情」把面板里的表情抓下来）。
4. **发送计划** 页 → 建计划：设时间、抖动分钟数、选目标好友、选文案来源。
5. 计划页点「立即执行」可以先试一次，确认能发出去再交给定时器。

---

## 关键设计

### 为什么"当天是否已发送"而不是"火花天数"来决定是否已续

火花可能已经熄灭了 —— 读到的天数不代表"今天点过火"。所以**是否已续**只看
「今天有没有发送成功的记录」（`send_job` 表里 `plan_date = 今天 AND state = 'sent'`）。
火花天数只作为**展示信息**给你看，不参与判断。

### 火花天数从哪读

抖音 Web 私信页的会话列表里，每个好友条目上就带着火花徽标：

```
.conversationConversationItemtitle      ← 好友昵称
  └── .commonStreaknormalText           ← 火花天数（形如 "🔥 81"）
```

一次 `page.evaluate` 就能把整张列表读出来。右侧会话面板顶部还有一份
（`.RightPanelHeadertitleContainer .commonStreaknormalText`），进会话时可以用它复核。

### 登录态怎么保持

用 Playwright 的 `launch_persistent_context`，每个账号一个独立的 profile 目录。
好处是登录一次长期有效，而且多账号之间 cookie / 缓存互不串。

**登录地和发送地必须是同一台机器** —— 这是规避风控最有效的一条。社区里用
GitHub Actions 挂机的方案成功率低，就是因为机房 IP + 异地登录触发了风控。

### 发送成功怎么确认（最容易写错的一环）

抖音是**先渲染气泡、后确定发送状态**的：气泡先出来，旁边转个圈，之后才变成
成功或者翻成红色的"重试"。所以"看到气泡了"绝不能当成"发送成功"。

`server/app/modules/douyin_spark/verifier.py` 里实现了一个状态机：

```
刚出现的干净气泡 → 观察一个完整的宽限窗口（2.5s）
                 ├─ 出现失败标记 → FAILED
                 ├─ 出现转圈     → 等它消失 → 要求稳定干净 0.6s → SUCCESS
                 └─ 整个窗口都干净 → SUCCESS
```

**不确定（超时）也按失败处理** —— 宁可报错让人来看，也不要谎报成功。

### 表情包怎么发

```
点表情按钮 (svg.messageMsgInputiconAction)
  → 表情面板 (.componentsemojiemojiPanel)
    → 切分类 (可选，面板默认只渲染第一屏)
    → 按名字找表情项 (.emojiEmojiItememojiItem / .emojiEmojiItememojiItemDesc)
    → 点击
```

⚠️ 点表情**只是把它插进了输入框**，抖音有时会直接发、有时要再点一次发送按钮。
`senders.py` 里对此有专门处理。

### 选择器为什么单独一个文件

抖音前端改版是常态而非意外。选择器散落在业务代码里，改一次版就要满仓库找。
现在全部集中在 `server/app/modules/douyin_spark/selectors.py`，并且支持用同目录
下的 `selectors.yaml` 覆盖 —— **改版时只改配置，不动代码**。

---

## 配置

所有配置都走环境变量（也可以放 `server/.env`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DSW_PORT` | `8766` | 后端端口 |
| `DSW_HEADLESS` | `true` | 浏览器无头模式（登录时强制有头） |
| `DSW_BROWSER_CHANNEL` | `chrome` | 用本机 Chrome / `msedge` / `chromium` |
| `DSW_BROWSER_EXECUTABLE` | 空 | 指定浏览器可执行文件路径 |
| `DSW_CHAT_URL` | `https://www.douyin.com/chat` | 私信页地址 |
| `DSW_DAILY_SEND_CAP` | `60` | 单账号每日发送上限 |
| `DSW_PER_FRIEND_DAILY_CAP` | `1` | 单个好友每日次数上限 |
| `DSW_QUIET_HOURS_START` / `_END` | 空 | 勿扰时段，如 `23:00` / `08:00` |
| `DSW_SEND_INTERVAL_MIN/MAX_SECONDS` | `3` / `8` | 两条消息之间的随机间隔 |
| `DSW_RETRY_LIMIT` | `2` | 失败重试次数 |

---

## 目录结构

```
server/
├── app/
│   ├── main.py                 FastAPI 入口
│   ├── settings.py             配置
│   ├── models.py               SQLite 模型
│   ├── db.py                   数据库会话
│   └── modules/
│       ├── registry.py         模块注册表
│       └── douyin_spark/
│           ├── selectors.py    所有 DOM 选择器（可被 yaml 覆盖）
│           ├── browser.py      Playwright 上下文 / 登录态
│           ├── chat_page.py    私信页页面对象
│           ├── senders.py      文字 / 图片 / 表情发送
│           ├── verifier.py     发送终态确认
│           ├── sticker_catalog.py  表情面板采集
│           ├── executor.py     任务执行
│           ├── service.py      业务逻辑
│           ├── planner.py      调度
│           └── router.py       HTTP 接口
└── data/                       运行时数据（git 忽略）
    ├── workbench.db            SQLite
    ├── profiles/               各账号的浏览器 profile
    └── artifacts/              失败截图、上传的图片

web/
└── src/
    ├── App.tsx                 布局 + 路由
    ├── api.ts                  API 客户端
    ├── store.tsx               当前账号上下文
    └── pages/                  各页面
```

---

## 加一个新模块

```python
# server/app/modules/<your_module>/router.py
router = APIRouter(prefix="/api/<your-module>", tags=["<your-module>"])

# server/app/main.py
from app.modules.your_module.router import router as your_router
app.include_router(your_router)

registry.register(registry.ModuleSpec(
    id="your-module",
    name="你的模块",
    description="...",
    icon="📦",
    prefix="/api/your-module",     # 接口前缀
    route_prefix="/your-module",   # 前端地址前缀，不能以 /api 开头
    nav=[{"key": "main", "label": "主页"}],
))
```

`prefix` 和 `route_prefix` 必须分开：浏览器地址只要落在 `/api/...` 下，刷新时
就会被后端当成接口请求（返回 422 而不是页面）。

前端加一个 `pages/YourModule.tsx` 并在 `App.tsx` 的 `Routes` 里挂上
`/your-module/main` 就行 —— 侧边栏导航是 `/api/modules` 生成的，不用改。

---

## 风险提示

自动化操作不在抖音用户协议允许的范围内，可能触发验证、限流或封号。

- 只操作你自己拥有、且不重要的账号
- 保持低频（一天一条足够），不要用于批量营销
- 登录地和发送地保持同一台机器
- 代码里**没有**打码平台接入、也没有任何绕过设备检测的逻辑 —— 走的是
  "正常账号 + 拟人行为 + 低频节奏"的路线

`douyin-keeper`（社区项目）里的一句话说得挺准：

> 拿去搞批量营销出了事别找我。
