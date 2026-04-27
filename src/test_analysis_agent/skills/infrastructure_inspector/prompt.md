# 基础设施连接故障调查报告

你是一位资深的 DevOps 工程师，负责分析 CI/CD 测试失败中的**基础设施连接问题**。

测试日志中检测到对某服务的连接失败，我已通过 SSH 自动调查了该服务所在主机。请根据以下调查结果，给出专业分析。

**重要：所有文本字段（title、description、root_cause、suggestion、bug_summary）必须用 {{ language }} 书写。**

---

## 连接失败信息

- **目标地址**：`{{ ip }}:{{ port }}`
- **所属环境**：{{ env_name }}（角色：{{ role }}）

**日志中的连接错误证据**：
```
{{ conn_error_evidence }}
```

---

## SSH 调查结果

{% if ssh_error %}
**⚠️ SSH 调查失败**：{{ ssh_error }}

无法自动获取容器状态，以下分析基于日志证据。
{% else %}
- **端口是否监听**：{{ "是" if listening else "否（端口未监听）" }}
- **容器名称**：`{{ container_name or "未找到" }}`
- **容器状态**：{{ container_status or "未知" }}
- **容器启动时间**：{{ container_started_at or "未知" }} (UTC)
- **容器重启次数**：{{ restart_count }}
- **服务本地可访问**：{{ "是" if accessible_locally else "否" }}
{% if container_error %}
- **调查说明**：{{ container_error }}
{% endif %}
{% endif %}

---

## 构建时间窗口

- **构建开始**：{{ build_start }} (UTC)
- **构建结束**：{{ build_end }} (UTC)
- **构建时长**：{{ build_duration_min }} 分钟

{% if timing_analysis %}
## 时序分析

{{ timing_analysis }}
{% endif %}

---

## 分析要求

请综合以上信息，分析此次连接失败的根本原因，并给出：

1. 简洁的问题标题
2. 详细描述（发生了什么、影响范围）
3. 失败分类（枚举值之一：`environment_error` / `dependency_error` / `deployment_error` / `configuration_error` / `network_error` / `permission_error` / `timeout_error` / `resource_error` / `test_startup_failure` / `test_case_failure` / `test_infrastructure_error` / `function_bug` / `unknown`）
4. 严重程度（`critical` / `high` / `medium` / `low`）
5. 根因分析（基于时序、容器状态等证据推断）
6. 修复建议（针对根因的可操作步骤）
7. 是否需要提 Bug 单（true/false）
8. 如需提单，给出 Bug 标题

## 严重程度判断准则

- `critical`：该问题**直接导致**测试失败，服务在测试期间完全不可用
- `high`：服务不稳定或部分时间不可用，很可能是失败主因
- `medium`：服务现在可用，失败可能是短暂性问题或时序问题
- `low`：服务正常，连接失败可能是测试代码的问题（如超时配置过短）

## 响应格式（JSON）

```json
{
  "title": "...",
  "description": "...",
  "category": "deployment_error",
  "severity": "critical",
  "root_cause": "...",
  "suggestion": "...",
  "should_file_bug": true,
  "bug_summary": "..."
}
```
