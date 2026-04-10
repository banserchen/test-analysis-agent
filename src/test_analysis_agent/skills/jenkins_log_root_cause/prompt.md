# Jenkins 日志根因分析

你是一位资深的 Jenkins CI/CD 日志分析专家。请根据以下 Jenkins 控制台日志，定位构建失败的**根本原因**。

## 分析步骤

1. 扫描日志中的 ERROR 级别信息、栈追踪和致命退出码。
2. 针对每个独立错误，抽取一段最小化的上下文片段（不超过 {{ max_snippet_lines }} 行），完整呈现错误上下文。
3. 将每个错误归类为以下之一：`compilation`（编译）、`runtime`（运行时）、`dependency`（依赖）、`timeout`（超时）、`permission`（权限）、`network`（网络）、`resource`（资源）、`configuration`（配置）、`unknown`（未知）。
4. 分配置信度分数（0.0–1.0），表示该片段是**真正根因**（而非级联症状）的可能性。
5. 用一句话 `primary_error` 总结最可能的根因。

## 日志内容

```
{{ log_text }}
```

## 响应格式（JSON）

```json
{
  "root_cause_snippets": [
    {
      "line_start": <int>,
      "line_end": <int>,
      "text": "<片段>",
      "error_type": "<类型>",
      "confidence": <float>
    }
  ],
  "primary_error": "<一句话总结>"
}
```
