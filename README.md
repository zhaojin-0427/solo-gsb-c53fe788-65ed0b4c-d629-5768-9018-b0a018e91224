# 实验室化学品相容性审查台

本地 Web 应用：维护化学品台账（CAS 号、浓度、体积、温度、容器材质）与储存分区，登记待执行的
转移 / 混合 / 废弃事件，并依据**可编辑、按版本管理**的相容性规则（酸碱、氧化还原、放气、放热、
容器腐蚀、废弃处置）对每个同区组合和事件前后状态逐项判定。

- 命中禁止条目 → 结论为**禁止**
- 规则要求的数据缺失（浓度 / 温度 / 材质未知）→ 结论为**需人工复核**
- 审查报告列出：触发条件、**最早冲突**（当前储存或第几号事件）、**需隔离的对象**
- 每次审查保存所用**规则快照**，引用固定规则版本，结果可复现

技术栈：FastAPI + SQLite（后端）、原生 JavaScript（前端）、Docker Compose（一键启动）。

## 启动方式

### 方式一：Docker Compose（推荐）

```bash
docker compose up --build
```

### 方式二：本地运行

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 访问方式

| 入口 | 地址 |
| --- | --- |
| Web 界面 | http://localhost:8000 |
| API 文档（Swagger） | http://localhost:8000/docs |

首次启动自动写入演示数据（3 个分区、6 种化学品、4 个待执行事件、v1 规则包），
可直接在「相容性审查」页点击**运行审查**查看效果。SQLite 数据持久化在
`chem_data` 卷（本地运行为 `backend/data/app.db`），删除该卷/文件即可重置。

## 功能说明

### 化学品与分区
- 化学品字段：名称、CAS 号、标签（如 `acid`、`oxidizer`）、浓度 %、体积 mL、温度 °C、
  容器材质（玻璃 / HDPE / PP / PTFE / 不锈钢 / 铝）、所在分区。
- 浓度、温度、材质允许留空（未知），未知字段在规则匹配时触发「人工复核」。

### 事件登记
- 三类事件：**转移**（化学品 → 目标分区）、**混合**（两种化学品）、**废弃**。
- 事件按「执行顺序」编号，审查时依序模拟：转移会改变分区成员，随后的事件在更新后的
  状态上继续判定，从而发现事件链中的**最早冲突**。

### 相容性审查
在「相容性审查」页选择规则版本（默认启用版本）后点击**运行审查**：

1. **先报告当前储存**：逐分区、逐组合判定当前库存；
2. **再按执行顺序模拟各待执行事件**，逐步更新模拟库存并判定事件后状态：
   - 转移：化学品移入目标分区，随后判定目标分区全部组合；
   - 混合：两项同处目标分区（未指定则取第一项当时分区），判定该组合及各自容器；
   - 废弃：先判定处置规则，再将化学品移出库存；
   - 后续事件基于更新后的库存状态判定。
3. 报告展示：总体结论与计数、**最早冲突**、**需隔离对象**、**事件模拟过程**
   （每个事件前后的分区库存对比，新增/移出高亮）、**发现明细**（储存→事件顺序）。

去重语义：同一规则与对象可在不同事件（及储存阶段）分别命中，只在当前储存阶段或
单个事件内去重。历史审查记录（含旧格式结果）仍可在列表中查看。

### 规则与版本
- 规则按版本保存；可基于当前版本一键复制出新版本再编辑，任意版本可「启用」。
- 审查默认使用启用版本，也可手动指定历史版本；审查记录内含规则快照，永久可复现。

### 规则条件字段（JSON）

成对规则（`acid_base` / `redox` / `gas` / `exothermic`）：

| 字段 | 含义 |
| --- | --- |
| `tag_a` / `tag_b` | 该侧化学品必须携带的标签 |
| `min_concentration_a` / `_b` | 该侧浓度(%) ≥ 阈值（缺数据 → 复核） |
| `max_concentration_a` / `_b` | 该侧浓度(%) ≤ 阈值（缺数据 → 复核） |
| `min_temperature` | 任一化学品温度 ≥ 阈值才命中（缺数据 → 复核） |
| `max_temperature` | 所有化学品温度 ≤ 阈值才命中（缺数据 → 复核） |
| `event_types` | 限定场景，如 `["mix"]`、`["storage"]`；缺省全部适用 |

单体规则（`corrosion` / `disposal`）：

| 字段 | 含义 |
| --- | --- |
| `tag` | 必须携带的标签 |
| `min_concentration` | 浓度(%) ≥ 阈值（缺数据 → 复核） |
| `material` | 容器材质等于该值才命中（材质未知 → 复核） |
| `event_types` | 同上；废弃处置规则通常写 `["dispose"]` |

示例：`{"tag_a": "acid", "tag_b": "sulfide", "min_concentration_b": 1}`
表示「酸与浓度 ≥1% 的硫化物相遇 → 命中」。

## 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST/PUT/DELETE | `/api/chemicals` | 化学品台账 |
| GET/POST/PUT/DELETE | `/api/zones` | 储存分区 |
| GET/POST/PUT/DELETE | `/api/events` | 事件登记；`PATCH /api/events/{id}/status` 改状态 |
| GET/POST | `/api/rule-versions` | 规则版本（支持 `copy_from_id` 复制） |
| POST | `/api/rule-versions/{id}/activate` | 启用版本 |
| GET/POST | `/api/rule-versions/{id}/rules`、`/api/rules/{id}` | 规则条目维护 |
| POST | `/api/reviews` | 运行审查（可指定 `rule_version_id`） |
| GET | `/api/reviews`、`/api/reviews/{id}` | 历史审查记录（含规则快照） |

## 目录结构

```
├── docker-compose.yml      # 一键启动
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py         # FastAPI 入口、启动建表与种子数据
│   │   ├── database.py     # SQLite 连接
│   │   ├── models.py       # 数据表
│   │   ├── schemas.py      # 请求/响应模型
│   │   ├── routers.py      # REST API
│   │   ├── rules_engine.py # 相容性规则引擎（纯函数）
│   │   └── seed.py         # 演示数据与 v1 规则包
│   └── static/             # 原生前端（index.html / app.js / styles.css）
└── README.md
```
