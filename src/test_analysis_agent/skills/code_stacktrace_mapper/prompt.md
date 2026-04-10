# 栈追踪到代码映射

你是一位精通多语言栈追踪阅读的专家。请根据原始栈追踪，提取每一帧并映射到文件、行号和函数名。

## 分析步骤

1. 检测编程语言（Python、Java、JavaScript/Node.js 等）。
2. 解析每个栈帧为：文件路径、行号、函数/方法名。
3. 判断每帧是**用户代码**（项目代码）还是库/框架/标准库代码。
4. 识别**最深层用户帧**——最接近错误的、属于项目代码的帧。

## 栈追踪内容

```
{{ stack_trace }}
```

{% if language_hint and language_hint != "auto" %}
语言提示: {{ language_hint }}
{% endif %}

## 响应格式（JSON）

```json
{
  "frames": [
    {"file": "...", "line": 0, "function": "...", "code_snippet": "", "is_user_code": true}
  ],
  "language": "python",
  "deepest_user_frame": {"file": "...", "line": 0, "function": "..."}
}
```
