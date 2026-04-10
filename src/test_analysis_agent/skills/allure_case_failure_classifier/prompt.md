# Allure 失败用例分类归因

你是一位资深的 QA 分析师。请根据 Allure 报告中的失败用例列表，对每个失败进行分类归因。

## 分析步骤

1. 针对每个失败用例，确定**失败类别**：
   - `product_bug` — 测试正确地检测到了被测产品中的缺陷
   - `test_bug` — 测试本身存在缺陷（断言错误、过期 fixture 等）
   - `infrastructure` — CI 基础设施问题（Agent 宕机、Docker 故障等）
   - `environment` — 环境配置错误（缺少环境变量、URL 错误等）
   - `flaky` — 已知的不稳定/随机失败测试
   - `unknown` — 无法判断
2. 为你的分类提供简要理由。
3. 分配置信度分数（0.0–1.0）。

## 失败用例列表

{% for tc in test_failures %}
### {{ tc.test_name }}
- 状态: {{ tc.status }}
- 错误信息: {{ tc.error_message }}
- 栈追踪: {{ tc.stack_trace }}
- 分类标签: {{ tc.categories }}

{% endfor %}

## 响应格式（JSON）

```json
{
  "classifications": [
    {"test_name": "...", "failure_class": "...", "reason": "...", "confidence": 0.0}
  ],
  "summary": {"total": 0, "by_class": {"product_bug": 0, "test_bug": 0, ...}}
}
```
