# Docker SRE Agent

极简 Docker 监控 agent — 检测容器故障，自动重启。

## 工作原理

监听 Docker 事件流（`die`、`oom`、`unhealthy`），检测到异常后自动重启容器。

重启策略三级升级：
1. `docker restart` — 优雅重启
2. `docker stop` + `docker start` — 强制重启
3. 放弃 — 连续失败 3 次后停止，等人工介入

两级限速防止重启风暴：
- 每个容器每小时最多 5 次
- 全局每小时最多 20 次

## 快速开始

### 直接运行

```bash
pip install -e .
docker-sre --config config.yaml
```

### Docker 部署

```bash
docker compose up -d
```

## 配置

编辑 `config.yaml`：

```yaml
monitor:
  exclude_containers:
    - "docker-sre-agent"    # 排除自身
  watch_containers: []      # 空 = 监控所有

restart:
  max_per_container_per_hour: 5
  max_global_per_hour: 20
  timeout: 10
  max_consecutive_fails: 3
```

## 依赖

- Python 3.11+
- `docker` (Python SDK)
- `pyyaml`
