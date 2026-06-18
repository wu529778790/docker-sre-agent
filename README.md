# Server SRE Agent

轻量级服务器自动运维 Agent — 面向 2核2G 小水管云服务器。

## 功能

- 🔄 **容器自动重启** — 监听 Docker 事件，容器挂了自动拉起，限速防风暴
- 🗑️ **磁盘清理** — 扫描 Docker + 宿主机磁盘占用，LLM 分析哪些能清
- 📊 **资源监控** — CPU/内存/磁盘使用率，容器资源排行
- 🌐 **Web 界面** — 手机查看服务器状态，聊天问答

## 快速开始

### 配置 .env

```bash
cp .env.example .env
# 编辑 .env，填入你的配置
```

### Docker 部署（推荐）

```bash
docker compose up -d
```

### 直接运行

```bash
pip install -e .

# Web 界面
server-sre web --port 6700

# 守护进程（事件监听 + 定时扫描）
server-sre run
```

## 架构

```
Docker 容器
├── 规则引擎 — 处理简单任务（快+免费）
├── LLM 分析 — 处理复杂情况（可选）
├── Web UI — 手机查看状态
├── Scanner — Docker 事件监听 + 自动重启
├── Scheduler — 定时磁盘扫描 + 资源检查
└── 工具层
    ├── Docker 工具 — 通过 socket 管理容器
    └── 宿主机工具 — 通过临时容器执行命令
```

## 工具

| 工具 | 作用 |
|------|------|
| `docker_info` | Docker 磁盘占用分析 |
| `docker_clean` | 清理 Docker 垃圾 |
| `container_list` | 列出所有容器状态 |
| `container_restart` | 重启容器 |
| `host_exec` | 在宿主机执行命令（只读） |
| `host_disk_scan` | 宿主机磁盘扫描 |

## 配置

编辑 `config.yaml`：

```yaml
monitor:
  exclude_containers: ["server-sre-agent"]
  check_interval: 300

restart:
  max_per_container_per_hour: 5
  max_consecutive_fails: 3

cleanup:
  mode: "report"    # report=只报告, auto=自动清理

llm:
  enabled: true
  model: "claude-sonnet-4-20250514"

web:
  port: 6700
```

## 依赖

- Python 3.9+
- Docker
- Anthropic API Key（可选，不配置则只用规则引擎）
