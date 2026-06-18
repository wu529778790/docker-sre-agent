# Docker SRE Agent

AI 驱动的 Docker 运维 Agent — 自动重启、智能扫描、清理建议。

## 功能

- **自动重启** — 容器挂了自动重启，限速防风暴
- **定时扫描** — 每小时 Docker 垃圾扫描，每天全盘扫描
- **AI 分析** — Claude 分析扫描结果，给出清理建议
- **交互问答** — 直接问 agent 关于服务器的任何问题

## 快速开始

### 配置

```bash
# 设置 API Key
export ANTHROPIC_API_KEY="your-key-here"

# 或在 config.yaml 中配置
```

### 运行

```bash
pip install -e .

# 守护进程模式 — 自动扫描 + 容器监控
docker-sre run --config config.yaml

# 交互问答
docker-sre ask "服务器上有什么垃圾"

# 一次性扫描
docker-sre scan
```

### Docker 部署

```bash
ANTHROPIC_API_KEY=xxx docker compose up -d
```

## 命令

| 命令 | 说明 |
|------|------|
| `docker-sre run` | 守护进程，定时扫描 + 容器监控 |
| `docker-sre ask "问题"` | 问答模式，问完退出 |
| `docker-sre scan` | 一次性扫描，输出报告 |

## 配置

编辑 `config.yaml`：

```yaml
scheduler:
  scan_interval: 3600           # AI 扫描间隔（秒）
  deep_scan_interval: 86400     # 全盘扫描间隔（秒）

llm:
  model: "claude-sonnet-4-20250514"
  max_tool_rounds: 10

cleanup:
  mode: "report"                # report=只报告, auto=自动清理
```

## 依赖

- Python 3.9+
- Docker
- Anthropic API Key
