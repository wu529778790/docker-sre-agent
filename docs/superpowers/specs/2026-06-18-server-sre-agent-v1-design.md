# Server SRE Agent v1 — Design Spec

## Goal

轻量级服务器 SRE agent，面向 2核2G 小水管腾讯云服务器。自动维护 10+ 个 Docker 容器，清理磁盘，监控资源。规则 + AI 混合决策。

## Target Users

有轻量云服务器的开发者，跑着 10+ 个 Docker 容器，不想手动维护。

## Target Environment

- 2核 2GB 内存
- 50GB 磁盘（可能用了 35G+）
- 10+ Docker 容器在跑
- 腾讯云轻量服务器

## Architecture

```
Docker 容器 (server-sre-agent)
├── 规则引擎 — 处理简单任务（快+免费）
├── LLM 分析 — 处理复杂情况（可选，有 key 才用）
├── Web UI — 手机查看状态
├── 调度器 — 事件监听 + 定时任务
└── 工具层
    ├── Docker 工具 — 通过 socket 操作
    └── 宿主机工具 — 通过临时容器执行命令
```

### 宿主机访问方案

通过 Docker socket 创建临时容器，挂载宿主机目录只读：

```python
def host_exec(command: str) -> str:
    """在宿主机上执行命令（通过临时容器）"""
    client = docker.from_env()
    container = client.containers.run(
        "alpine:latest",
        command=["sh", "-c", command],
        volumes={"/": {"bind": "/host", "mode": "ro"}},
        remove=True,
        detach=False,
    )
    return container.logs().decode()
```

这样 agent 能运行 `du`、`ls`、`df` 等任何命令查看宿主机，但不能修改文件。

### 工具清单

| 工具 | 作用 | 访问方式 | 读/写 |
|------|------|---------|-------|
| `docker_info` | Docker 磁盘占用分析 | Docker socket | 读 |
| `docker_clean` | 清理 Docker 垃圾 | Docker socket | 写 |
| `container_list` | 列出所有容器状态 | Docker socket | 读 |
| `container_restart` | 重启容器 | Docker socket | 写 |
| `host_disk_scan` | 宿主机磁盘扫描 | 临时容器 | 读 |
| `host_exec` | 在宿主机执行命令 | 临时容器 | 可配置 |
| `resource_monitor` | CPU/内存/磁盘监控 | 临时容器 | 读 |

### 安全设计

- `host_exec` 默认只读模式（挂载 `/host:ro`）
- 只有明确的清理命令才允许写操作
- 所有操作有审计日志
- 白名单机制：只有预定义的命令能执行
- Web UI 有 token 认证

## v1 Features

### 1. 磁盘清理

**痛点：** 50G 磁盘用了 35G，不知道什么占空间。

**方案：**
- 扫描 Docker 磁盘占用（`docker system df`）
- 扫描宿主机大文件（`find /host -size +100M`）
- 找到最大的目录和文件
- LLM 分析哪些能清、哪些不能
- 执行清理（用户确认后）

### 2. 容器自动重启

**痛点：** 容器挂了不知道，网站挂了才发现。

**方案：**
- 监听 Docker 事件流
- 检测 die/oom/unhealthy 事件
- 自动重启（限速：每容器每小时最多 5 次）
- 连续失败 3 次停止，等人工介入

### 3. 资源监控

**痛点：** 不知道服务器负载情况。

**方案：**
- CPU/内存/磁盘使用率
- 各容器资源占用排行
- 异常检测（内存泄漏、CPU 飙升）
- 定时报告（每小时/每天）

### 4. Web 界面

**功能：**
- 查看服务器状态
- 查看容器列表和状态
- 手动触发扫描
- 聊天问答（可选，需要 LLM）

## Deployment

### Docker Compose

```yaml
version: "3.8"
services:
  server-sre:
    image: server-sre-agent:latest
    container_name: server-sre-agent
    restart: unless-stopped
    ports:
      - "6700:6700"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/containers:/host/containers:ro
      - /var/lib/docker/volumes:/host/volumes:ro
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - WEB_TOKEN=${WEB_TOKEN}
```

### systemd (alternative)

```bash
pip install server-sre-agent
systemctl enable --now server-sre
```

## Config

```yaml
agent:
  name: "server-sre"

monitor:
  exclude_containers: ["server-sre-agent"]
  check_interval: 300          # 5 分钟

restart:
  max_per_container_per_hour: 5
  max_consecutive_fails: 3

cleanup:
  mode: "report"               # report / auto
  exclude_paths: ["/etc", "/boot", "/usr"]

llm:
  model: "claude-sonnet-4-20250514"
  enabled: true

web:
  port: 6700
  token: "${WEB_TOKEN}"
```

## File Structure

```
server_sre_agent/
├── __init__.py
├── main.py           # 入口
├── config.py         # 配置加载
├── agent.py          # ReAct 循环
├── llm.py            # LLM 客户端
├── scheduler.py      # 定时任务
├── scanner.py        # Docker 事件监听
├── web.py            # Web 界面 + MCP
├── prompts.py        # System prompt
├── docker_client.py  # 共享 Docker 客户端
├── tools/
│   ├── base.py       # Tool 基类
│   ├── docker.py     # Docker 工具
│   ├── host.py       # 宿主机工具（临时容器）
│   ├── cleanup.py    # 清理工具
│   └── monitor.py    # 资源监控工具
└── templates/
    └── chat.html     # Web 界面
```

## Dependencies

```toml
dependencies = [
    "docker>=7.0.0",
    "pyyaml>=6.0",
    "anthropic>=0.40.0",
    "flask>=3.0.0",
]
```

## Memory Budget

目标：常驻内存 < 100MB

- Python 进程本身：~30MB
- Docker SDK：~10MB
- Flask：~10MB
- 其他：~10MB
- 余量：~40MB

## What NOT to Do (v1)

- ❌ 不做 MCP（以后加）
- ❌ 不做安全巡检（以后加）
- ❌ 不做日志分析（以后加）
- ❌ 不做告警通知（以后加）
- ❌ 不做多服务器管理（以后加）
