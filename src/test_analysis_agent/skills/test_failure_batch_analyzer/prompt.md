# 测试失败组深度分析

你是一位资深的测试分析师和质量工程师。以下 **{{ count }} 个测试失败**均来自**同一个测试脚本文件**，且具有**相同的错误根因**（已通过 heuristic 归为同一组）。

{% if sample_count < count %}
**注意：因用例数量较多，以下仅展示 {{ sample_count }} 个代表性样本，但 affected_tests 列表须涵盖全部 {{ count }} 个用例名称。**
{% endif %}

**重要：所有文本字段（title、description、root_cause、suggestion、bug_summary）必须用 {{ language }} 书写。**

## 你的任务

对这一组失败进行深度分析，输出 **1 个 issue**（若同时存在测试脚本缺陷和产品功能缺陷，则输出 **2 个 issue**）。

## 分类说明

- `function_bug` — **被测产品的功能缺陷**。`AssertionError` 中包含业务断言（如"车辆未重新规划路径"、"任务超时"）= 产品问题。
- `test_case_failure` — **测试脚本本身的缺陷**。`UnboundLocalError`/`NameError` = 变量未初始化（测试脚本问题）。
- 其他：`test_infrastructure_error`、`environment_error`、`network_error`、`timeout_error`、`configuration_error`、`unknown`

**双重问题规则**：若 `UnboundLocalError` 是由产品行为未触发导致（如车辆未到达指定区域 → 变量从未赋值），须输出 2 个 issue：
1. `test_case_failure`：脚本未处理产品行为未触发的情况（变量未初始化）
2. `function_bug`：产品功能未按预期执行（车辆未到达区域）

**级联失败规则**：若部分用例因"先前任务超时/失败导致后续未执行"，将所有级联失败合并入根因 issue，说明级联影响范围。

## 失败用例样本

{% for f in failures %}
--- 样本 {{ loop.index }} ---
用例名: {{ f.test_name }}
状态: {{ f.status }}
错误: {{ f.error_message }}
栈追踪: {{ f.stack_trace }}

{% endfor %}

## 输出格式（JSON 数组，1 或 2 个元素）

```json
[
  {
    "title": "简洁标题，描述根因（不超过30字）",
    "description": "详细描述：表现、影响范围、受影响用例数量",
    "category": "function_bug | test_case_failure | test_infrastructure_error | network_error | timeout_error | environment_error | configuration_error | unknown",
    "severity": "critical | high | medium | low",
    "affected_tests": ["全部受影响用例名称列表"],
    "root_cause": "根因分析（2-4句）",
    "suggestion": "修复建议（具体可操作）",
    "should_file_bug": true,
    "bug_summary": "缺陷单标题（should_file_bug 为 true 时填写）或 null"
  }
]
```
