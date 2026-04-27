# 测试分析 Agent（Test Analysis Agent）

> 面向使用者的中文说明。英文版请见 [`README.md`](README.md)；
> 面向 AI Agent 二次开发的技术文档请见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

一个基于 AI 的 **CD 流水线失败分析 Agent**：自动读取 Jenkins 构建日志和
Allure 测试报告，定位根因，并生成结构化的分析报告与提 Bug 建议。

---

## ✨ 功能特性

- **流水线阶段识别** — 通过 Jenkins Workflow API（wfapi）获取权威的 stage 列表，使用真实的 stage 名称定位失败；同时抓取每个 stage 的步骤日志，为根因分析提供精准上下文
- **Jenkins 集成** — 通过 Jenkins API 直接拉取构建日志、wfapi stage 数据以及 Jenkinsfile 内容
- **Allure 报告分析** — 解析 Allure JSON 报告，提取并分析失败用例
- **LLM 驱动的根因分析** — 可插拔 LLM 后端，支持任意 OpenAI 兼容接口以及 GitHub Copilot SDK
- **可扩展的技能系统** — 以 Python 插件形式添加自定义分析技能，适配业务领域模式
- **环境基础设施检查** — 当检测到网络/连接错误时，可选通过 SSH 登录测试环境主机，检查容器与服务健康状态
- **飞书云文档** — 将分析结果生成结构化的飞书云文档，包含模块版本信息、测试用例统计、问题分析（表格）及 Bug 上报建议（表格）
- **多种报告格式** — 支持 Markdown、JSON、HTML
- **双形态接入** — 既是 CLI（适合在 Jenkins 流水线里调用），也是 HTTP API（适合部署成服务）
- **多语言报告** — 支持中文（zh-CN）和英文（en）

---

## 🏗️ 架构概览

```
┌──────────────────────────────────────────────────────────┐
│                    Analysis Agent                        │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Jenkins  │  │   Allure     │  │   Skill Registry   │  │
│  │ Parser   │  │   Parser     │  │ ┌──────────────┐   │  │
│  │          │  │              │  │ │ 日志规则匹配 │   │  │
│  └────┬─────┘  └──────┬───────┘  │ │ 自定义技能   │   │  │
│       │               │          │ └──────────────┘   │  │
│       └───────┬───────┘          └────────┬───────────┘  │
│               │                           │              │
│         ┌─────▼───────────────────────────▼─────┐        │
│         │           LLM 分析引擎                 │        │
│         │ ┌──────────────┬──────────────────┐   │        │
│         │ │ OpenAI API   │ Copilot SDK      │   │        │
│         │ └──────────────┴──────────────────┘   │        │
│         └─────────────┬──────────────────────────┘       │
│                       │                                  │
│              ┌────────▼────────┐                         │
│              │   报告生成器    │                         │
│              │ (MD/JSON/HTML)  │                         │
│              └─────────────────┘                         │
└──────────────────────────────────────────────────────────┘
         ▲                              ▲
         │                              │
    ┌────┴────┐                  ┌──────┴──────┐
    │  CLI    │                  │  HTTP API   │
    │(Jenkins)│                  │  (FastAPI)  │
    └─────────┘                  └─────────────┘
```

---

## 🚀 快速开始

### 安装

```bash
pip install -e .
```

### 配置

复制示例配置文件并填入你自己的参数：

```bash
cp .env.example .env
# 修改 .env，填入 Jenkins 和 LLM 相关的凭证
```

所有环境变量都以 `TAA_` 开头，常用项如下：

| 变量名 | 说明 | 默认值 |
|----------|-------------|---------|
| `TAA_LLM_PROVIDER` | LLM 提供方：`openai` 或 `copilot` | `openai` |
| `TAA_LLM_API_KEY` | OpenAI 的 API Key，或 Copilot 使用的 GitHub Token | （必填） |
| `TAA_LLM_BASE_URL` | LLM API 的 Base URL（仅 OpenAI 方式使用） | `https://api.openai.com/v1` |
| `TAA_LLM_MODEL` | 使用的模型名 | `gpt-4o` |
| `TAA_JENKINS_URL` | Jenkins 服务器地址 | （分析 Jenkins 时必填） |
| `TAA_JENKINS_USERNAME` | Jenkins 用户名 | |
| `TAA_JENKINS_PASSWORD` | Jenkins API Token | |
| `TAA_REPORT_LANGUAGE` | 报告语言（`zh-CN` 或 `en`） | `zh-CN` |
| `TAA_SKILLS_DIR` | 自定义技能插件目录 | |
| `TAA_ENVIRONMENTS_API_URL` | 测试环境 API 的基础 URL；启用后支持通过 SSH 检查环境基础设施 | |
| `TAA_FEISHU_APP_ID` | 飞书应用的 App ID，用于创建云文档 | |
| `TAA_FEISHU_APP_SECRET` | 飞书应用的 App Secret | |
| `TAA_FEISHU_FOLDER_TOKEN` | 飞书文档保存的目标文件夹 Token | |

### LLM 后端选项

通过 `TAA_LLM_PROVIDER` 切换：

#### OpenAI 兼容接口（默认）

支持 OpenAI、Azure OpenAI，以及任何 OpenAI 兼容的自建/第三方网关：

```bash
TAA_LLM_PROVIDER=openai
TAA_LLM_API_KEY=sk-你的-openai-key
TAA_LLM_BASE_URL=https://api.openai.com/v1   # 或 Azure / 自建网关地址
TAA_LLM_MODEL=gpt-4o
```

#### GitHub Copilot SDK

使用 [GitHub Copilot SDK](https://github.com/github/copilot-sdk) 通过 Copilot 提供的 LLM
能力来执行分析。需要安装 Copilot CLI，并拥有 GitHub Copilot 订阅（或已配置 BYOK）。

```bash
# 安装 Copilot 可选依赖
pip install -e ".[copilot]"

# 配置 Agent
TAA_LLM_PROVIDER=copilot
TAA_LLM_API_KEY=ghp_你的-github-token   # 如果 Copilot CLI 已登录，可不填
TAA_LLM_MODEL=gpt-5.4
```

---

## 🖥️ 命令行使用

### 分析一次 Jenkins 构建失败

```bash
# 基本用法
test-analysis-agent analyze "my-project/deploy-pipeline" 42

# 带 Allure 报告一起分析
test-analysis-agent analyze "my-project/deploy-pipeline" 42 \
  --allure-path /path/to/allure-report \
  --test-repo /path/to/test-source-code

# 输出 HTML 到文件
test-analysis-agent analyze "my-project/deploy-pipeline" 42 \
  --format html -o report.html

# 使用远程 Allure 报告 URL
test-analysis-agent analyze "my-project/deploy-pipeline" 42 \
  --allure-url https://allure.example.com/report/42
```

### 直接分析一个日志文件

```bash
test-analysis-agent analyze-log /path/to/console-output.log \
  --job-name "my-project" --build-number 42
```

### 启动 API 服务

```bash
test-analysis-agent serve --port 9090
```

### 查看已注册的技能

```bash
test-analysis-agent skills
```

---

## 🌐 HTTP API 使用

启动服务：

```bash
test-analysis-agent serve
```

### 分析一次 Jenkins 流水线

```bash
curl -X POST http://localhost:9090/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "job_name": "my-project/deploy-pipeline",
    "build_number": 42,
    "allure_report_path": "/path/to/allure-report"
  }'
```

### 分析原始日志文本

```bash
curl -X POST http://localhost:9090/api/v1/analyze/log \
  -H "Content-Type: application/json" \
  -d '{
    "log_text": "... jenkins 控制台日志 ...",
    "job_name": "my-project",
    "build_number": 42
  }'
```

### 指定报告格式

```bash
# Markdown
curl -X POST http://localhost:9090/api/v1/analyze/report/markdown \
  -H "Content-Type: application/json" \
  -d '{"job_name": "my-project", "build_number": 42}'

# HTML
curl -X POST http://localhost:9090/api/v1/analyze/report/html \
  -H "Content-Type: application/json" \
  -d '{"job_name": "my-project", "build_number": 42}'
```

### 在线 API 文档

服务启动后，可在 `http://localhost:9090/docs` 查看 Swagger 交互式 API 文档。

### 创建飞书云文档

```bash
# 将分析结果 JSON 发送到飞书接口，创建云文档
curl -X POST http://localhost:9090/api/v1/report/feishu \
  -H "Content-Type: application/json" \
  -d '{ <AnalysisReport JSON> }'
# 返回：{"document_url": "https://your-tenant.feishu.cn/docx/..."}

# 列出所有历史飞书报告
curl http://localhost:9090/api/v1/reports/feishu
# 返回：{"documents": [...], "count": N}
```

飞书文档内容包含：模块版本信息、测试用例统计、问题分析（表格）、Bug 上报建议（表格）。飞书凭证通过 `TAA_FEISHU_APP_ID` 和 `TAA_FEISHU_APP_SECRET` 配置。

---

## 🔧 Jenkins 流水线集成

在 `Jenkinsfile` 的失败钩子里调用：

```groovy
pipeline {
    // ... 你的流水线 stage ...

    post {
        failure {
            script {
                // 方式 1：CLI 集成
                sh """
                    test-analysis-agent analyze \
                        "${env.JOB_NAME}" ${env.BUILD_NUMBER} \
                        --allure-path ${WORKSPACE}/allure-report \
                        --format html -o failure-report.html
                """
                archiveArtifacts artifacts: 'failure-report.html'

                // 方式 2：HTTP API 集成
                def response = httpRequest(
                    url: 'http://analysis-agent:9090/api/v1/analyze',
                    httpMode: 'POST',
                    contentType: 'APPLICATION_JSON',
                    requestBody: """{
                        "job_name": "${env.JOB_NAME}",
                        "build_number": ${env.BUILD_NUMBER},
                        "allure_report_url": "${ALLURE_REPORT_URL}"
                    }"""
                )
                writeFile file: 'analysis-report.json', text: response.content
            }
        }
    }
}
```

---

## 🐳 Docker 部署

```bash
# 构建镜像
docker build -t test-analysis-agent .

# 以 API 服务方式运行
docker run -d --name analysis-agent \
  -p 9090:9090 \
  -e TAA_LLM_API_KEY=你的-key \
  -e TAA_JENKINS_URL=https://jenkins.example.com \
  -e TAA_JENKINS_USERNAME=user \
  -e TAA_JENKINS_PASSWORD=token \
  test-analysis-agent
```

---

## 🔍 分析流程

Agent 会按以下路径进行分析：

1. **Stage 定位**
   - 调用 Jenkins Workflow API（`wfapi/describe`）获取权威的 stage 列表（真实名称 + 状态）
   - 通过 replay 接口拉取 Jenkinsfile，了解各 stage 的脚本内容
   - 找到第一个真正失败的 stage（排除因上游失败而被跳过的 stage）
   - 输出结果中只包含失败/跳过的 stage，通过的 stage 会被过滤掉

2. **非测试阶段失败**
   - 通过 wfapi 节点日志接口抓取该 stage 内失败步骤的详细日志
   - 如果该 stage 触发了下游 Job，会递归拉取并分析下游 Job 的日志
   - 将 stage 日志 + 脚本上下文交给 LLM 进行根因分析

3. **测试阶段失败**
   - **测试未启动**（没有用例跑起来）：分析 Jenkins 日志中的启动期错误
   - **测试已执行**：自动识别 `{build_url}/allure` 处的 Allure 报告，逐个解析失败用例，交给 LLM 分析，再按根因聚合归并

4. **报告生成**
   - 以真实的 stage 名称汇总所有结论，生成结构化报告
   - 针对值得提单的问题生成包含 `summary`（标题）和 `detail_description`（详情）的 Bug 建议

---

## 🧩 自定义技能

通过继承 `BaseSkill` 添加领域专属的分析技能：

```python
# my_skills/k8s_analyzer.py
from test_analysis_agent.skills.base import BaseSkill

class KubernetesAnalyzerSkill(BaseSkill):
    name = "k8s_analyzer"
    description = "分析 Kubernetes 相关的部署失败"
    version = "1.0.0"

    def can_handle(self, context):
        log_text = context.get("log_text", "")
        return "kubectl" in log_text or "kubernetes" in log_text.lower()

    def execute(self, context):
        log_text = context.get("log_text", "")
        # 这里写你自己的 K8s 日志分析逻辑
        findings = analyze_k8s_logs(log_text)
        return {"k8s_findings": findings}
```

把自定义技能放到某个目录下，通过 `TAA_SKILLS_DIR=/path/to/my_skills`
（或 API 参数）告诉 Agent 即可自动加载。

---

## 🛠️ 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 跑测试
pytest tests/ -v

# 代码检查
ruff check src/ tests/

# 代码格式化
ruff format src/ tests/
```

如需深入了解代码结构、模块职责和扩展方式，请阅读
[`ARCHITECTURE.md`](ARCHITECTURE.md)。

---

## 📄 许可证

MIT
