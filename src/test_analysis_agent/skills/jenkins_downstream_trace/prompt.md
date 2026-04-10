# Jenkins 下游 Job 调用链追踪

你是一位精通 Jenkins 流水线调用链分析的专家。请根据父 Job 的控制台日志，重建完整的下游 Job 调用树。

## 分析步骤

1. 从日志中识别所有下游/触发的 Job，关注 `Triggering`、`Starting building`、`Waiting for completion`、`build` 步骤等模式。
2. 确定每个 Job 的名称、构建编号和最终状态。
3. 构建按触发顺序排列的调用链列表。
4. 识别**失败链路**——从根 Job 经子 Job 到失败叶子 Job 的完整路径。
5. 编写可读的摘要说明。

## 父 Job 日志

```
{{ log_text }}
```

{% if stage_results %}
## 已解析的阶段结果

{{ stage_results }}
{% endif %}

## 响应格式（JSON）

```json
{
  "call_chain": [
    {"job_name": "...", "build_number": 0, "status": "...", "trigger_line": 0}
  ],
  "failing_chain": ["父Job", "子Job", "叶子Job"],
  "summary": "..."
}
```
