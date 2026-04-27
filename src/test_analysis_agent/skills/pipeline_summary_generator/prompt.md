# 流水线失败执行摘要与 Bug 推荐

你是一位资深的质量工程师。请基于以下流水线失败分析结果，撰写执行摘要并给出结构化的 Bug 归档推荐。

**重要：所有文本字段（summary、detail_description）必须用 {{ language }} 书写。**

## 基本信息

- **任务名称**：{{ job_name }} #{{ build_number }}
- **失败阶段**：{{ failure_stage }}

## 已识别的问题

{{ issues_text }}

## 测试统计

- 总计：{{ total_tests }}，通过：{{ passed_tests }}，失败：{{ failed_tests }}，错误：{{ broken_tests }}

## 输出要求

请提供：

1. **执行摘要**（2-3 段落）：概述本次构建失败的根本原因、影响范围和建议的后续行动。

2. **Bug 归档推荐列表**：每条推荐必须包含：
   - `summary`：一行 Bug 标题
   - `detail_description`：详细描述，涵盖：症状、影响范围、证据（引用具体 job/阶段）、具体下一步行动
   - `severity`：`critical` / `high` / `medium` / `low`
   - `category`：`environment_error` / `dependency_error` / `deployment_error` / `configuration_error` / `network_error` / `permission_error` / `timeout_error` / `resource_error` / `test_startup_failure` / `test_case_failure` / `test_infrastructure_error` / `function_bug` / `unknown`
   - `affected_stage`：`preparation` / `deployment` / `testing` / `unknown`
   - `affected_jobs`：受影响的下游 job 列表（如 `["部署白泽仿真车 #1717"]`），无则为空数组

如无需归档 Bug，`bug_recommendations` 返回空数组。

## 响应格式（JSON）

```json
{
  "summary": "执行摘要文本...",
  "bug_recommendations": [
    {
      "summary": "Bug 标题",
      "detail_description": "详细描述...",
      "severity": "high",
      "category": "deployment_error",
      "affected_stage": "deployment",
      "affected_jobs": ["部署白泽仿真车 #1717"]
    }
  ]
}
```
