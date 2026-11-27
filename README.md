# 员工 AI 助手单独关停开关（global / site / role / tool 四级）

会员空间平台「员工 AI 助手入口」模块的紧急关停能力。关闭助手**不影响**招商 AI 等其他 AI 模块，反之亦然。

- 技术栈：Python 3.11 / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL 16 / Redis（测试用 fakeredis）
- 交付内容：开关表与迁移、纯函数判定、Redis 缓存与主动失效、状态/对话/工具/管理四类接口、SSE 流式关停、审计与消息中心桩、测试、紧急关停手册、运维控制台

## 一、判定顺序（定死，不可调整）

```
ASST_FORCE_OFF=true  →  global  →  site  →  role  →  tool
（环境变量，无条件关闭）  （全平台）  （站点）  （角色编码）  （工具名）
```

任一级关闭即生效，命中即停，不再向下判定。四级判定实现在 `app/switch_domain.py:evaluate()`，
是**纯函数**：输入调用者上下文（用户、角色、站点、目标工具）与已加载的开关行，输出判定结果，
不碰数据库、不碰 Redis，因此顺序规则可以脱离 I/O 单独测。

| 级别 | scope | scope_ref | 例 |
| --- | --- | --- | --- |
| 全局 | `global` | 空字符串 | 全平台演练 |
| 站点 | `site` | site_id | `site-hangzhou` |
| 角色 | `role` | 角色编码 | `operation` |
| 工具 | `tool` | 工具名 | `sop_draft` |

## 二、依赖与运行环境

### 需要什么
- Python 3.11+（实测 3.11.9）
- PostgreSQL 16（实测 16.4）；Redis 6+（生产必选，测试不需要真实 Redis）
- Python 包见 `requirements.txt`（运行）与 `requirements-dev.txt`（测试/静态检查）

### 不需要什么
- 不需要真实 Redis 服务：未配置 `ASST_REDIS_URL` 时使用进程内缓存 `InMemorySwitchCache`，
  与 Redis 后端实现同一接口（`SwitchCache` 协议）；Redis 分支由 fakeredis 在测试中覆盖。
- 不需要 S2-IAM / S2-AUDIT 真实服务：`app/iam.py`、`app/audit.py` 是接口桩（见「七、对接点」）。
- 不需要消息中心：`app/notify.py` 是内存 outbox 桩。
- 不需要任何密钥：所有配置只从环境变量读取，仓库里没有令牌。测试里的 `tok-*` 是虚构身份的固定字符串。

### 怎么跑起来（逐条命令 + 预期输出）

```bash
# 1) 依赖（干净环境从零安装，实测约 40 秒）
python -m venv _venv
_venv/Scripts/python -m pip install -r requirements-dev.txt
# 预期末行：Successfully installed ... fastapi-0.136.1 ... uvloop... （无 ERROR）

# 2) 数据库（本机私有实例，端口 55432，与机器上其它 PostgreSQL 不冲突）
C:/pgsql16/bin/initdb -D _pgdata -U postgres --pwfile=_pgpw.txt -A scram-sha-256 -E UTF8 --locale=C
C:/pgsql16/bin/pg_ctl -D _pgdata -l _pg.log -o "-p 55432" -w start
# 预期：server started / waiting for server to start.... done

C:/pgsql16/bin/createdb -h localhost -p 55432 -U postgres asst_killswitch
export ASST_DATABASE_URL='postgresql+psycopg://postgres:<本地口令>@localhost:55432/asst_killswitch'
python -m alembic upgrade head
# 预期：Running upgrade  -> 20261127_0001, asst_switch, recruit_switch and audit_log

# 3) 服务（控制台与接口同源）
export ASST_REDIS_URL=''          # 留空即用进程内缓存
python scripts/serve.py
# 预期：starting on http://127.0.0.1:8087  cache=memory  ttl=5.0s  force_off=False
# 打开 http://127.0.0.1:8087/console      运维控制台
# 打开 http://127.0.0.1:8087/docs         OpenAPI 文档
```

全部配置项（`.env.example` 有同名示例）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ASST_DATABASE_URL` | `sqlite+pysqlite:///./var/asst.db` | 数据库连接串 |
| `ASST_REDIS_URL` | 空 | 留空用进程内缓存 |
| `ASST_CACHE_TTL_SECONDS` | `5` | 开关快照 TTL |
| `ASST_SSE_FRAME_INTERVAL_SECONDS` | `0.05` | 流式帧间隔 |
| `ASST_SSE_MAX_FRAMES` | `600` | 单条流最大帧数 |
| `ASST_FORCE_OFF` | `false` | `true/1/yes/on` 生效；`false/0/` 为空即关闭该能力 |
| `ASST_FORCE_OFF_REASON` | 空 | 强关期间 `status` 里展示的原因 |

### 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/assistant/status` | 当前身份的助手状态：`enabled`、`source`、展示文案、可用/被停用工具 |
| POST | `/api/assistant/chat` | 对话；`stream=true` 返回 `text/event-stream` |
| GET | `/api/assistant/tools` | 可用工具与被停用工具 |
| POST | `/api/assistant/tools/{name}/invoke` | 调用工具；工具级关闭时 409 `TOOL_UNAVAILABLE` |
| GET/PUT | `/api/assistant/admin/switches` | 开关列表 / 变更（仅系统管理员） |
| GET | `/api/assistant/admin/audit` | 审计流水 |
| GET | `/api/assistant/admin/notifications` | 消息中心发件箱 |
| GET | `/api/assistant/admin/cache-keys` | 缓存后端、代际号与 TTL |
| GET | `/api/recruitment/ai/tasks`、POST `.../tasks/{id}/run` | 招商 AI 桩（用于验证模块隔离） |
| GET | `/console` | 运维控制台页面 |

## 三、跑测试

```bash
python -m pytest -q
# 预期：72 passed, 1 warning in ~17s（该 warning 来自第三方库 starlette 的 httpx 弃用提示）

python -m ruff check app tests alembic scripts
# 预期：All checks passed!

python -m mypy app tests
# 预期：Success: no issues found in 31 source files

python scripts/smoke_e2e.py        # 需要 ASST_DATABASE_URL 指向真实 PostgreSQL
# 预期：SMOKE_RESULT=PASS checks=9
```

测试不需要 Redis、不需要外网：SSE 用例会在本机起一个真实 uvicorn（随机端口）并用 httpx 流式读取。

## 四、验收标准与实现位置

| AC | 场景 | 实现 | 测试 |
| --- | --- | --- | --- |
| 1 | `ASST_FORCE_OFF=true` + 断库，对话仍 503 `ASSISTANT_DISABLED` | `app/resolver.py` 第一行读环境变量即返回 | `tests/test_force_off.py::test_force_off_survives_a_dead_database` |
| 2 | 关闭 global 后，进行中的 SSE 5 秒内以 `finish_reason=disabled` 结束 | `app/sse.py` 每帧边界检查 | `tests/test_sse.py::test_in_flight_stream_ends_within_five_seconds` |
| 3 | 关闭某站点：该站点拒绝，其他站点正常 | `app/switch_domain.py:evaluate()` 站点循环 | `tests/test_site_scope.py`（6 例） |
| 4 | 关助手后招商 AI 任务接口仍 200 | 独立表、独立缓存键前缀、独立审计前缀 | `tests/test_module_isolation.py`（5 例） |

## 五、缓存与失效

- 键名按模块分前缀，两套开关永不共键：`asst:switch:gen`、`asst:switch:data:<generation>`、
  频道 `asst:switch:invalidated`；招商 AI 侧对应 `recruit:*`。
- 每次变更 `INCR` 代际号（`asst:switch:gen`）并发布一条失效消息。代际号进了缓存键，
  所以**读方看到新代际就不可能再读旧快照**——传播延迟是一次 Redis 往返，不依赖 TTL。
- 快照本身仍带 5 秒 TTL。即使失效消息全部丢失（网络分区、订阅方掉线），
  最坏 5 秒后也一定是新值。两条机制叠加：主动失效负责「立刻」，TTL 负责「兜底」。
- 缓存不可用时**失败关闭**（fail closed）：`source=state_unavailable`，助手暂停而非放开。
  这条与强关分支是两回事，见下。

## 六、三个坑（本单最容易踩错的三处）

**坑 1：强关分支里还去读了一次 Redis。**
反例写法：`if force_off: return off` 之前先 `cache.get(...)` 拿开关快照。数据库已经断了，
这一读会在断库场景下抛异常——本该是 503 `ASSISTANT_DISABLED`，结果变成 500。
本实现里 `SwitchResolver.resolve()` 的第一段就是环境变量判断，命中直接构造 `Decision` 返回，
之后**既不建数据库连接、也不发 Redis 命令**；`tests/test_force_off.py` 用一个指向 59999 端口的
引擎做断言：`connects=0, statements=0, sessions=0`，缓存计数 `total=0`。

**坑 2：等整段生成完再判断开关。**
反例写法：`for token in generate(): yield token` 循环结束后才看开关。一段回复动辄十几秒，
等它生成完再写 `finish_reason` 早就超过 5 秒了。
本实现把检查放在**每一帧的边界**（`app/sse.py`）：先取一次代际号（一次 Redis 读），
代际号没变就复用上一条快照，变了才回库读一次；判定为关就立即写 `finish` 帧并结束生成器。
实测流中关闭到收到结束帧的延迟为**百毫秒级**（见 `docs/checks/`）。

**坑 3：两套开关共用同一个缓存键。**
反例写法：助手与招商 AI 都用 `switch:global`。关掉助手时把键写成 `off`，招商 AI 读到同一个键，
跟着一起停——AC4 直接挂。
本实现所有键都带模块前缀（`asst:` / `recruit:`），审计动作也带前缀
（`assistant.switch.*` / `recruitment.switch.*`），测试里用同一个 fakeredis 实例验证键不相交。

## 七、对接点（现在用桩，接入真实服务时只改一处）

| 依赖 | 现状 | 接入位置 |
| --- | --- | --- |
| S2-IAM（身份、角色、数据范围） | `app/iam.py` 固定目录，`Bearer tok-*` → 虚构账号 | `IamDirectory.authenticate()` 换成真实校验；令牌解析在 `app/deps.py:bearer_token()` |
| S2-AUDIT（审计日志） | 写本地 `audit_log` 表 | `app/repository.py:write_audit()` 换成 HTTP 调用，字段见 `app/audit.py:AuditEvent` |
| 平台消息中心 | 内存 outbox | `app/notify.py:NotificationOutbox.send()` |
| 招商 AI | `app/recruit/routes.py` 极简桩（两个任务） | 真实模块接自己那张表与缓存前缀，助手侧无需改动 |

## 八、目录

```
app/
  config.py          环境变量与 Settings
  db.py              engine、会话、数据库访问计数器（AC1 断言用）
  models.py          asst_switch / recruit_switch / audit_log（含统一字段）
  switch_domain.py   纯函数判定：四级顺序、判定结果、展示文案
  cache.py           Redis 与进程内两种缓存实现、代际号、失效发布
  resolver.py        环境变量 → 缓存 → 数据库的读取顺序与失败关闭
  sse.py             流式生成，每帧边界检查开关
  asst/{switch,routes}.py   服务对象与 HTTP 层
  recruit/routes.py  招商 AI 桩（模块隔离）
  iam.py audit.py notify.py 三个接口桩
  console/index.html 运维控制台（纯静态，无外部资源）
alembic/versions/20261127_0001_asst_switch.py   建表迁移（含 CHECK 约束与初始行）
docs/emergency-off.md                            紧急关停操作手册
scripts/serve.py                                 本地启动
scripts/smoke_e2e.py                             对真实 PostgreSQL 的端到端冒烟
tests/                                           72 个用例
```

## 九、数据与保密

示例数据全部虚构：企业名（星澜科技、云栖生物、恒泰智造）、人名（周敏、林澈、何然、苏禾、唐宁）、
站点（site-hangzhou / site-suzhou）均为演示用，不对应任何真实主体。仓库与文档中不出现客户名称。
