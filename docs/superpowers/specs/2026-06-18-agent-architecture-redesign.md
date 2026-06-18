# Agent Architecture Redesign — ReAct + LLM

## Goal

将 docker-sre-agent 从「固定规则的重启脚本」升级为「自动运行的 AI 运维 Agent」。常驻进程，定时扫描，LLM 分析，自动报告/清理。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                  docker-sre-agent (常驻进程)              │
│                                                          │
│  ┌──────────────┐     ┌──────────────┐                  │
│  │ Scheduler     │     │ Event Listener│                  │
│  │ 定时扫描      │     │ 容器事件监听   │                  │
│  │ (每 N 小时)   │     │ (实时)        │                  │
│  └──────┬───────┘     └──────┬───────┘                  │
│         │                     │                          │
│         ▼                     ▼                          │
│  ┌──────────────────────────────────────┐               │
│  │           Agent Loop (ReAct)         │               │
│  │                                      │               │
│  │  扫描结果 → Claude API (tool_use)    │               │
│  │       │                              │               │
│  │       ▼                              │               │
│  │  LLM 决定：调工具 / 给出建议          │               │
│  │       │                              │               │
│  │       ▼                              │               │
│  │  输出：报告 / 清理建议 / 自动执行     │               │
│  └──────────────────────────────────────┘               │
│                                                          │
│  ┌──────────────────────────────────────┐               │
│  │           工具层                      │               │
│  │  只读：scan_docker, scan_disk, ...   │               │
│  │  写入：run_command (白名单+确认)      │               │
│  └──────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

## 两种运行模式

### 1. 自动模式（默认）— 常驻进程

```bash
docker-sre run --config config.yaml
```

- 后台常驻，定时触发扫描
- 扫描结果发给 LLM 分析
- mode=report：只记录日志，不执行清理
- mode=auto：LLM 建议 → 自动清理白名单内的项目
- 同时监听容器事件，自动重启挂掉的容器

### 2. 交互模式 — 按需问答

```bash
docker-sre ask "服务器上有什么垃圾"
docker-sre ask "nginx 容器的日志能清理吗"
```

- 一次性运行，回答完退出
- 可以追问，多轮对话

## 定时任务

```yaml
scheduler:
  # 自动重启检查（继承原有功能）
  quick_check_interval: 30      # 秒 — 容器状态检查

  # AI 扫描任务
  scan_interval: 3600           # 秒 — Docker 垃圾扫描（默认 1 小时）
  deep_scan_interval: 86400     # 秒 — 全盘扫描（默认 24 小时）
```

**扫描流程**：
```
定时触发 → 收集信息(scan_docker + scan_disk) → 发给 LLM → LLM 输出建议
    ↓
mode=report → 记录到日志
mode=auto  → 白名单内的自动清理，需要确认的等用户操作
```

## 文件结构

```
docker_sre_agent/
├── __init__.py
├── main.py           # 入口：run / ask / scan
├── config.py         # 配置加载
├── agent.py          # ReAct 循环 + 工具调度
├── llm.py            # Claude API 封装（tool_use）
├── scheduler.py      # 定时任务调度
├── scanner.py        # Docker 事件监听 + 自动重启
├── tools/
│   ├── __init__.py
│   ├── base.py       # Tool 基类
│   ├── docker.py     # scan_docker, inspect_container
│   ├── disk.py       # scan_disk, scan_resources
│   └── command.py    # run_command（白名单+确认）
└── prompts.py        # System prompt 模板
```

## 工具定义

### 只读工具（LLM 随便调）

**scan_docker** — Docker 全景扫描
```json
{
  "name": "scan_docker",
  "description": "扫描 Docker 环境：停止的容器、悬空镜像、未使用卷、构建缓存大小",
  "input_schema": { "type": "object", "properties": {} }
}
```

**scan_disk** — 磁盘扫描
```json
{
  "name": "scan_disk",
  "description": "扫描磁盘：大文件、旧日志、包缓存。返回占用空间最大的前 N 项",
  "input_schema": {
    "type": "object",
    "properties": {
      "top_n": { "type": "integer", "default": 20 }
    }
  }
}
```

**scan_resources** — 容器资源占用
```json
{
  "name": "scan_resources",
  "description": "获取所有运行中容器的 CPU、内存、磁盘 IO 占用",
  "input_schema": { "type": "object", "properties": {} }
}
```

**inspect_container** — 查看容器详情
```json
{
  "name": "inspect_container",
  "description": "查看指定容器的详细信息：配置、挂载、网络、日志摘要",
  "input_schema": {
    "type": "object",
    "properties": {
      "name": { "type": "string" }
    },
    "required": ["name"]
  }
}
```

### 写入工具（必须用户确认）

**run_command** — 执行清理命令
```json
{
  "name": "run_command",
  "description": "执行系统命令（仅限清理相关）。自动模式下白名单内自动执行，其他需确认。",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": { "type": "string" },
      "reason": { "type": "string" }
    },
    "required": ["command", "reason"]
  }
}
```

## System Prompt

```
你是一个专业的 Linux/SRE 运行专家，驻守在一台服务器上。你的职责：
1. 监控服务器资源使用情况
2. 发现并清理无用的 Docker 资源和磁盘垃圾
3. 确保服务器健康运行

工作流程：
1. 分析提供的扫描数据
2. 识别可以安全清理的资源
3. 按回收空间大小排序给出建议
4. 标注风险等级：安全删除 / 需确认 / 不建议删除

安全规则：
- 绝对不删除系统关键文件（/etc, /boot, /usr）
- 不删除正在运行的容器
- 不删除 7 天内有变化的数据卷
- 建议必须给出理由
```

## 配置

```yaml
agent:
  name: "docker-sre-agent"

scheduler:
  quick_check_interval: 30
  scan_interval: 3600           # AI 扫描间隔
  deep_scan_interval: 86400     # 全盘扫描间隔

monitor:
  exclude_containers:
    - "docker-sre-agent"

restart:
  max_per_container_per_hour: 5
  max_global_per_hour: 20
  timeout: 10
  max_consecutive_fails: 3

llm:
  api_key: "${ANTHROPIC_API_KEY}"
  model: "claude-sonnet-4-20250514"
  max_tool_rounds: 10

cleanup:
  mode: "report"              # report = 只报告, auto = 自动清理白名单内项目
  exclude_paths:
    - "/etc"
    - "/boot"
    - "/usr"
  exclude_containers:
    - "docker-sre-agent"
  auto_clean:                  # mode=auto 时自动执行的命令
    - "docker system prune -f"
    - "docker volume prune -f"
```

## 安全设计

1. **工具白名单** — LLM 只能调用预定义工具
2. **命令白名单** — run_command 只允许特定命令
3. **自动模式限制** — auto 模式只执行 `auto_clean` 列表中的命令
4. **排除路径** — 关键系统目录永不触碰
5. **报告模式优先** — 默认只报告不执行
6. **循环上限** — 最多 10 轮工具调用
