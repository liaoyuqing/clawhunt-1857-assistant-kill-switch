# 员工 AI 助手紧急关停操作手册

> 目标：在**后台不可用、数据库可疑、Redis 不可用**的情况下，仍能把员工 AI 助手停掉。
> 三段动作按可用性从高到低排列：第一段不依赖任何外部组件，第二段只依赖数据库，第三段只依赖 Redis。

## 零、先判断该用哪一段

| 现场情况 | 用哪一段 | 生效时间 |
| --- | --- | --- |
| 完全不确定、后台打不开、需要立刻止血 | **一、环境变量强关** | 进程重启后立即，或下一轮请求（见下） |
| 后台能进但想跳过 UI、或多实例配置不便 | 二、数据库直改 | 5 秒内（TTL 兜底），主动失效则立即 |
| 只想让某几个缓存实例立刻刷新 | 三、缓存代际号 | 一次 Redis 往返 |

**不要**用「删表」「清空 asst_switch」当止血手段：删表等于把「未配置」当成「开启」，
助手会立刻恢复可用，且审计断链。

## 一、环境变量强关（不依赖数据库、不依赖 Redis、不依赖后台）

原理：`ASST_FORCE_OFF` 在 `SwitchResolver.resolve()` 的第一段读取，命中即返回关闭，
**不建数据库连接、不发 Redis 命令**。所以数据库断了、Redis 挂了都能停。

### 1. 打开强关

systemd 部署（推荐用 drop-in，不动主 unit 文件）：

```bash
sudo mkdir -p /etc/systemd/system/asst-api.service.d
sudo tee /etc/systemd/system/asst-api.service.d/99-force-off.conf >/dev/null <<'EOF'
[Service]
Environment=ASST_FORCE_OFF=true
Environment=ASST_FORCE_OFF_REASON=紧急止血：值班决定临时关闭员工AI助手，恢复时间待通知
EOF
sudo systemctl daemon-reload
sudo systemctl restart asst-api
```

容器部署：

```bash
kubectl -n member-space set env deploy/asst-api ASST_FORCE_OFF=true \
  ASST_FORCE_OFF_REASON='紧急止血：值班决定临时关闭员工AI助手'
kubectl -n member-space rollout status deploy/asst-api
# docker compose 同理：在 compose 文件里加 environment 后 docker compose up -d asst-api
```

裸进程 / 临时演练（不改配置，只影响当前进程）：

```bash
kill -TERM "$(cat /run/asst-api.pid)"
ASST_FORCE_OFF=true ASST_FORCE_OFF_REASON='演练' python scripts/serve.py
```

### 2. 验证（三条都要过）

```bash
# 2.1 状态：enabled=false，source=force_off（HTTP 200，不碰数据库）
curl -s -H 'Authorization: Bearer <任一员工令牌>' http://127.0.0.1:8087/api/assistant/status
# 期望：{"enabled":false,"source":"force_off","message":"员工 AI 助手已被管理员关闭...","reason":"紧急止血..."}

# 2.2 对话：HTTP 503 + ASSISTANT_DISABLED
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Authorization: Bearer <任一员工令牌>' -H 'Content-Type: application/json' \
  -d '{"prompt":"ping"}' http://127.0.0.1:8087/api/assistant/chat
# 期望：503

# 2.3 断库演练（可选，证明强关不依赖数据库）：把 ASST_DATABASE_URL 指向一个关闭的端口再重启，
#     上面两条仍应分别返回 200 / 503，服务器日志里不出现连接错误。
```

### 3. 回滚（恢复）

```bash
# 删掉 drop-in 并重启（不要只改 Environment 而不 reload）
sudo rm /etc/systemd/system/asst-api.service.d/99-force-off.conf
sudo systemctl daemon-reload
sudo systemctl restart asst-api
```

```bash
# 容器
kubectl -n member-space set env deploy/asst-api ASST_FORCE_OFF=false
kubectl -n member-space rollout status deploy/asst-api
```

回滚后必须复核：`GET /api/assistant/status` 返回 `enabled` 取决于**数据库里的开关行**，
而不是强关变量。若此前有人把 global 行改成了关闭，回滚强关不会自动恢复助手——需要同时处理第二段。
**回滚不等于恢复**，这是最容易出错的一步。

## 二、数据库直改（不依赖后台，依赖数据库可写）

适用：后台进不去、或强关不想动进程。表 `asst_switch` 上有 `(scope, scope_ref)` 唯一索引，
所以用 `INSERT ... ON CONFLICT` 是幂等的。

### 1. 关闭全局

```bash
psql "$ASST_DATABASE_URL" <<'SQL'
insert into asst_switch
  (id, scope, scope_ref, enabled, reason, changed_by, changed_at,
   org_id, site_id, created_at, created_by, updated_at)
values
  (gen_random_uuid(), 'global', '', false,
   '紧急止血：DBA 直接改库关闭，工单 CHG-0000', 'dba-oncall', now(),
   '', '', now(), 'dba-oncall', now())
on conflict (scope, scope_ref) do update
  set enabled = excluded.enabled,
      reason = excluded.reason,
      changed_by = excluded.changed_by,
      changed_at = now(),
      updated_at = now();
SQL
```

关某个站点 / 角色 / 工具：把 `scope` 与 `scope_ref` 换掉即可（`site` + `site-hangzhou`、
`role` + `operation`、`tool` + `sop_draft`）。`reason` 必填且不超过 200 字，超长会被 CHECK 约束拒绝。

### 2. 让所有实例立刻看到（可选，不做也在 5 秒内生效）

```bash
redis-cli INCR asst:switch:gen        # 代际号 +1 → 所有实例的缓存键全部失效
redis-cli PUBLISH asst:switch:invalidated asst
```

### 3. 验证

```bash
curl -s -H 'Authorization: Bearer <员工令牌>' http://127.0.0.1:8087/api/assistant/status
# 期望：enabled=false，source=global，reason 为上面写入的原因
```

### 4. 回滚

同一条 SQL，把 `false` 换成 `true`，`reason` 写明恢复依据（例如「故障排除，值班复核后恢复」）。
再执行第 2 步的 `INCR` 让缓存立刻刷新。

### 5. 补记审计与通知

直改库绕过了应用层，audit 表里没有这条记录。事后必须补一条（字段与
`app/audit.py:AuditEvent` 一致，`action` 用 `assistant.switch.disable` / `.enable`）：

```sql
insert into audit_log (id, module, action, target, actor, detail, org_id, site_id,
                       created_at, created_by, updated_at)
values (gen_random_uuid(), 'assistant', 'assistant.switch.emergency_db_patch', 'global',
        'dba-oncall', '紧急止血直改库，工单 CHG-0000', '', '', now(), 'dba-oncall', now());
```

并在管理员群补发一条消息（应用内的消息中心桩只记录应用层变更，直改库不会被它捕获）。

## 三、只刷缓存（依赖 Redis 可用）

```bash
redis-cli DEL asst:switch:gen                     # 下一个请求会把代际号重建为 1 并回库读
redis-cli --scan --pattern 'asst:switch:data:*' | xargs -r redis-cli DEL
```

**注意**：`DEL asst:switch:gen` 会让代际号**回到 1**。如果此前代际号已经涨到 37，
而旧快照 `asst:switch:data:1` 还在（TTL 未到），就可能读到一份陈旧快照。
稳妥做法是 `INCR`（第二步的写法）而不是 `DEL`；只有在确认 `asst:switch:data:*` 也一起清掉时才用 `DEL`。

## 四、Redis 完全不可用时

- 应用不会因此放开：缓存读失败 → 回落到「上一条快照」；连快照都没有 → `source=state_unavailable`，
  **助手暂停**（fail closed），不会误放行。
- 此时第二段（数据库直改）仍然有效：实例读不到缓存就会回库。
- 反过来说，数据库也不可用时，只有第一段（环境变量）能止血。

## 五、演练清单（建议每季度一次）

1. 在预发环境设 `ASST_FORCE_OFF=true` 并**把数据库端口指向关闭端口**，确认 `/api/assistant/status`
   返回 200 且 `enabled=false`、`chat` 返回 503；确认日志无连接错误。
2. 保持一条 SSE 流打开（`curl -N`），关闭 global，确认 5 秒内收到 `event: finish` 且
   `finish_reason=disabled`。
3. 关闭 `site-hangzhou`，确认杭州账号 503、苏州账号 200。
4. 关闭助手后调用 `/api/recruitment/ai/tasks`，确认仍 200。
5. 回滚强关，确认助手按数据库开关行恢复（而不是无条件恢复）。

## 六、常见误判

| 现象 | 真实原因 |
| --- | --- |
| 强关后 `status` 仍 `enabled=true` | 变量值写成了 `True`（大写 T）以外的形式没问题，但写成 `"1 "` 带空格可以；写成 `true1` 不行。用 `true/1/yes/on` 四选一 |
| 强关后 `chat` 返回 500 而不是 503 | 代码在强关分支里还访问了数据库或 Redis（坑 1） |
| 改了库但立刻查还是旧值 | 没 `INCR` 代际号，等 5 秒 TTL 即可；超过 5 秒还旧说明读的不是同一个 Redis |
| 关了助手，招商 AI 也停了 | 两套开关共用了缓存键或同一张表（坑 3） |
| 回滚强关后助手没恢复 | 数据库里的 global 行仍是 `enabled=false`，见「回滚不等于恢复」 |
