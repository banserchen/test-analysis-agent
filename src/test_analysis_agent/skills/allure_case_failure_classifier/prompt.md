# Allure 失败用例深度分析

你是一位资深的 QA 分析师。请对以下 Allure 测试失败用例进行深度分析，输出每个用例的失败分类和 AI 分析。

**重要：所有文本字段（ai_analysis、reason）必须用 {{ language }} 书写。**

## 分析目标

对每个失败用例输出：

1. **失败类别** (`failure_category`)，必须使用下列枚举值之一：
   - `function_bug` — **被测产品的功能缺陷**。测试代码正确地检测到了产品行为与预期不符。最典型的表现是 `AssertionError`：当断言消息描述的是产品行为（如"车辆未重新规划路径"、"状态码不匹配"），说明产品功能有问题，而非测试脚本有问题。
   - `test_case_failure` — **测试脚本本身存在缺陷**。包括：变量未初始化（`UnboundLocalError`、`NameError`）、fixture 错误、flaky 测试、测试数据准备失败等。注意：当 `UnboundLocalError` 出现时，通常意味着某个前置条件（如车辆到达指定位置）未被触发，此时需要同时考虑是否存在 `function_bug`（前置条件本身是产品行为）。
   - `test_infrastructure_error` — CI 基础设施问题（Agent 宕机、Docker 故障、Jenkins 异常等）
   - `environment_error` — 环境配置错误（缺少环境变量、URL 错误、依赖缺失等）
   - `test_startup_failure` — 测试启动失败（无法连接到设备、无法初始化测试环境等）
   - `network_error` — 网络连接/超时问题
   - `timeout_error` — 操作超时（但要区分：若超时是因为产品未到达预期状态，本质是 `function_bug`）
   - `configuration_error` — 配置文件或参数错误
   - `dependency_error` — 第三方依赖或服务不可用
   - `resource_error` — 资源不足（磁盘、内存、CPU 等）
   - `unknown` — 无法判断

   **判断原则：**
   - `AssertionError` + 业务断言消息 → `function_bug`
   - `UnboundLocalError` / `NameError` → `test_case_failure`（但若变量未初始化是由于产品行为未触发，在 ai_analysis 中同时指出潜在的 `function_bug`）
   - 超时 + "产品未到达预期状态" → `function_bug`

2. **AI 分析** (`ai_analysis`)：2-4 句话，解释该用例为何失败、失败的功能意义及建议修复方向。若存在双重问题（测试脚本问题 + 产品功能问题），需明确指出两方面。

3. **失败原因** (`reason`)：一句话简明说明分类理由

4. **置信度** (`confidence`)：0.0–1.0 的数值

## 失败用例（按错误分组）

{% for group in failure_groups %}
### 组 {{ loop.index }}（共 {{ group.count }} 个用例）

用例：
{% for name in group.test_names %}
- {{ name }}
{% endfor %}

状态：{{ group.status }}

错误信息：
```
{{ group.error_message }}
```

栈追踪：
```
{{ group.stack_trace }}
```

{% if group.source_code %}
测试脚本源码（供深度分析用）：
```python
{{ group.source_code }}
```

**源码分析要求（仅当提供源码时）：**
1. 理解该测试函数的功能意图（它在验证什么产品行为）
2. 识别测试脚本中的编程问题（变量未初始化、逻辑缺陷等）→ `test_case_failure`
3. 基于测试意图，判断是否存在潜在的产品功能问题（如超时等待说明产品行为未触发）→ `function_bug`
4. 若同时存在两类问题，在 ai_analysis 中明确区分，并用 `failure_category` 标注主要分类
{% endif %}

{% endfor %}

## 响应格式（JSON）

对每个用例（test_name 使用上面的完整用例名）返回一条记录：

```json
{
  "classifications": [
    {
      "test_name": "用例完整名称",
      "failure_category": "function_bug",
      "ai_analysis": "该用例失败原因是...",
      "reason": "检测到产品功能不符合预期",
      "confidence": 0.85
    }
  ],
  "summary": {
    "total": {{ total }},
    "by_category": {"function_bug": 0, "test_case_failure": 0, "test_infrastructure_error": 0, "environment_error": 0, "unknown": 0}
  }
}
```
