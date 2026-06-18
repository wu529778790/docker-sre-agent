# Minimal Docker SRE Agent — Design Spec

## Goal

一个极简的 Docker 监控 agent：监听容器事件，检测异常，自动重启。不超过 300 行代码。

## 文件结构

```
docker-sre-agent/
├── agent.py          # 核心逻辑：事件监听 + 重启处理 + 限速
├── config.py         # 加载 YAML 配置
├── main.py           # 入口 + 信号处理
├── config.yaml       # 默认配置
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 核心设计

### 1. 事件驱动，不轮询

使用 `docker SDK` 的 `client.events()` 流式监听 Docker 事件。零延迟、零 CPU 空转。

监听的事件类型：
- `die` — 容器退出
- `oom` — OOM killed
- `health_status: unhealthy` — 健康检查失败

### 2. 重启策略：三级升级

```
尝试 1: docker restart (优雅重启，10s timeout)
    ↓ 失败
尝试 2: docker stop + docker start (强制)
    ↓ 失败
放弃，记录日志，等待下次事件触发时重试
```

每次重启后重置该容器的失败计数。连续失败 3 次则标记为「需要人工介入」并停止自动重启。

### 3. 限速：滑动窗口

两级限速防止重启风暴：
- **per-container**: 每个容器每小时最多 5 次重启
- **全局**: 所有容器合计每小时最多 20 次重启

用 `collections.deque` 存储时间戳，滑动窗口淘汰旧记录。

### 4. 自动排除

默认排除自身容器（通过 hostname 或环境变量识别）。可配置额外排除列表。

### 5. 配置

```yaml
agent:
  name: "docker-sre-agent"

monitor:
  exclude_containers:
    - "docker-sre-agent"
  # 空 = 监控所有容器
  watch_containers: []

restart:
  max_per_container_per_hour: 5
  max_global_per_hour: 20
  timeout: 10            # restart timeout (seconds)
  max_consecutive_fails: 3  # 连续失败后停止自动重启

log:
  level: "INFO"
```

### 6. 日志

使用 Python `logging` 输出到 stdout，格式：
```
2026-06-18 12:00:00 [INFO] Container 'nginx' died (exit_code=137), restarting...
2026-06-18 12:00:02 [INFO] Container 'nginx' restarted successfully
```

### 7. 进程管理

- 不做 watchdog — 用 `docker compose restart: unless-stopped` 或 systemd 管理
- 信号处理：SIGTERM/SIGINT 优雅退出，关闭事件流

## 依赖

- `docker>=7.0.0` — Docker Python SDK
- `pyyaml>=6.0` — 配置解析
- Python 3.11+

## 代码量预估

| 文件 | 行数 |
|------|------|
| agent.py | ~120 行 |
| config.py | ~40 行 |
| main.py | ~30 行 |
| **总计** | **~190 行** |

## 不做的事

- ❌ SQLite / 知识库
- ❌ MCP 服务器
- ❌ 诊断引擎 / 模式学习
- ❌ Prometheus / metrics
- ❌ 告警通道（日志就够了）
- ❌ 健康检查端点
- ❌ 容器依赖感知
