# Test Analysis Agent

> 📘 中文使用文档 → [`README.zh-CN.md`](README.zh-CN.md) · 🤖 Agent-oriented technical doc → [`ARCHITECTURE.md`](ARCHITECTURE.md)

AI-powered agent for analyzing CD pipeline failures. Automatically identifies root causes from Jenkins build logs and Allure test reports, then generates structured analysis reports with bug filing recommendations.

## Features

- **Pipeline Stage Detection** — Automatically classifies failures into preparation, deployment, or testing stages
- **Jenkins Integration** — Fetches and parses build logs directly from Jenkins via API
- **Allure Report Analysis** — Parses Allure JSON reports to extract and analyze test failures
- **LLM-Powered Analysis** — Pluggable LLM backend supporting OpenAI-compatible APIs and GitHub Copilot SDK
- **Extensible Skill System** — Add custom analysis skills as Python plugins for domain-specific patterns
- **Multiple Output Formats** — Reports in Markdown, JSON, and HTML
- **Dual Interface** — CLI for Jenkins pipeline integration + HTTP API for service deployment
- **Multi-Language Reports** — Supports Chinese (zh-CN) and English report generation

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Analysis Agent                         │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │  Jenkins  │  │   Allure     │  │   Skill Registry   │ │
│  │  Parser   │  │   Parser     │  │  ┌──────────────┐  │ │
│  │          │  │              │  │  │ Log Patterns  │  │ │
│  └────┬─────┘  └──────┬───────┘  │  │ Custom Skills │  │ │
│       │               │          │  └──────────────┘  │ │
│       └───────┬───────┘          └────────┬───────────┘ │
│               │                           │             │
│         ┌─────▼───────────────────────────▼─────┐       │
│         │          LLM Analyzer                  │       │
│         │  ┌──────────────┬──────────────────┐   │       │
│         │  │ OpenAI API   │ Copilot SDK      │   │       │
│         │  └──────────────┴──────────────────┘   │       │
│         └─────────────┬──────────────────────────┘       │
│                       │                                  │
│              ┌────────▼────────┐                         │
│              │ Report Generator│                         │
│              │ (MD/JSON/HTML)  │                         │
│              └─────────────────┘                         │
└──────────────────────────────────────────────────────────┘
         ▲                              ▲
         │                              │
    ┌────┴────┐                  ┌──────┴──────┐
    │   CLI   │                  │  HTTP API   │
    │(Jenkins)│                  │  (FastAPI)  │
    └─────────┘                  └─────────────┘
```

## Quick Start

### Installation

```bash
pip install -e .
```

### Configuration

Copy the example environment file and fill in your settings:

```bash
cp .env.example .env
# Edit .env with your Jenkins and LLM API credentials
```

Key environment variables (all prefixed with `TAA_`):

| Variable | Description | Default |
|----------|-------------|---------|
| `TAA_LLM_PROVIDER` | LLM provider: `openai` or `copilot` | `openai` |
| `TAA_LLM_API_KEY` | API key for OpenAI or GitHub token for Copilot | (required) |
| `TAA_LLM_BASE_URL` | LLM API base URL (OpenAI provider only) | `https://api.openai.com/v1` |
| `TAA_LLM_MODEL` | Model name | `gpt-4o` |
| `TAA_JENKINS_URL` | Jenkins server URL | (required for Jenkins analysis) |
| `TAA_JENKINS_USERNAME` | Jenkins username | |
| `TAA_JENKINS_PASSWORD` | Jenkins API token | |
| `TAA_REPORT_LANGUAGE` | Report language (`zh-CN` or `en`) | `zh-CN` |
| `TAA_SKILLS_DIR` | Custom skills plugin directory | |

### LLM Provider Options

The agent supports two LLM backends, configured via `TAA_LLM_PROVIDER`:

#### OpenAI-compatible API (default)

Works with OpenAI, Azure OpenAI, and any OpenAI-compatible API endpoint:

```bash
TAA_LLM_PROVIDER=openai
TAA_LLM_API_KEY=sk-your-openai-key
TAA_LLM_BASE_URL=https://api.openai.com/v1  # or Azure/self-hosted URL
TAA_LLM_MODEL=gpt-4o
```

#### GitHub Copilot SDK

Uses the [GitHub Copilot SDK](https://github.com/github/copilot-sdk) to run analysis through Copilot's LLM capabilities. Requires the Copilot CLI and a GitHub Copilot subscription (or BYOK configuration).

```bash
# Install the optional Copilot dependency
pip install -e ".[copilot]"

# Configure the agent
TAA_LLM_PROVIDER=copilot
TAA_LLM_API_KEY=ghp_your-github-token  # optional if Copilot CLI is already authenticated
TAA_LLM_MODEL=gpt-4o
```

### CLI Usage

#### Analyze a Jenkins build failure

```bash
# Basic analysis
test-analysis-agent analyze "my-project/deploy-pipeline" 42

# With Allure report
test-analysis-agent analyze "my-project/deploy-pipeline" 42 \
  --allure-path /path/to/allure-report \
  --test-repo /path/to/test-source-code

# Output as HTML to a file
test-analysis-agent analyze "my-project/deploy-pipeline" 42 \
  --format html -o report.html

# With Allure report URL
test-analysis-agent analyze "my-project/deploy-pipeline" 42 \
  --allure-url https://allure.example.com/report/42
```

#### Analyze a log file directly

```bash
test-analysis-agent analyze-log /path/to/console-output.log \
  --job-name "my-project" --build-number 42
```

#### Start the API server

```bash
test-analysis-agent serve --port 8080
```

#### List registered skills

```bash
test-analysis-agent skills
```

### API Usage

Start the server:

```bash
test-analysis-agent serve
```

#### Analyze a Jenkins pipeline

```bash
curl -X POST http://localhost:8080/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "job_name": "my-project/deploy-pipeline",
    "build_number": 42,
    "allure_report_path": "/path/to/allure-report"
  }'
```

#### Analyze raw log text

```bash
curl -X POST http://localhost:8080/api/v1/analyze/log \
  -H "Content-Type: application/json" \
  -d '{
    "log_text": "... jenkins console output ...",
    "job_name": "my-project",
    "build_number": 42
  }'
```

#### Get report in specific format

```bash
# Markdown report
curl -X POST http://localhost:8080/api/v1/analyze/report/markdown \
  -H "Content-Type: application/json" \
  -d '{"job_name": "my-project", "build_number": 42}'

# HTML report
curl -X POST http://localhost:8080/api/v1/analyze/report/html \
  -H "Content-Type: application/json" \
  -d '{"job_name": "my-project", "build_number": 42}'
```

#### API Documentation

Interactive API docs are available at `http://localhost:8080/docs` when the server is running.

### Jenkins Pipeline Integration

Add to your Jenkinsfile post-failure step:

```groovy
pipeline {
    // ... your pipeline stages ...

    post {
        failure {
            script {
                // Option 1: CLI integration
                sh """
                    test-analysis-agent analyze \
                        "${env.JOB_NAME}" ${env.BUILD_NUMBER} \
                        --allure-path ${WORKSPACE}/allure-report \
                        --format html -o failure-report.html
                """
                archiveArtifacts artifacts: 'failure-report.html'

                // Option 2: API integration
                def response = httpRequest(
                    url: 'http://analysis-agent:8080/api/v1/analyze',
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

### Docker Deployment

```bash
# Build image
docker build -t test-analysis-agent .

# Run as API service
docker run -d --name analysis-agent \
  -p 8080:8080 \
  -e TAA_LLM_API_KEY=your-key \
  -e TAA_JENKINS_URL=https://jenkins.example.com \
  -e TAA_JENKINS_USERNAME=user \
  -e TAA_JENKINS_PASSWORD=token \
  test-analysis-agent
```

## Analysis Flow

The agent follows this analysis flow based on the failure stage:

1. **Preparation/Deployment Stage Failure**
   - Parses Jenkins console log to identify the failing stage
   - If the stage triggered downstream jobs, fetches and analyzes those job logs too
   - Sends error context to LLM for root cause analysis

2. **Testing Stage Failure**
   - **Test startup failure** (no tests executed): Analyzes Jenkins logs for startup errors
   - **Test execution failure** (tests ran): Parses Allure report, analyzes each failed test case individually with LLM (optionally including test source code), then groups failures by root cause

3. **Report Generation**
   - Combines all findings into a structured report
   - Generates bug filing recommendations for issues that warrant tickets

## Custom Skills

Extend the agent with custom analysis skills:

```python
# my_skills/k8s_analyzer.py
from test_analysis_agent.skills.base import BaseSkill

class KubernetesAnalyzerSkill(BaseSkill):
    name = "k8s_analyzer"
    description = "Analyzes Kubernetes-specific deployment failures"
    version = "1.0.0"

    def can_handle(self, context):
        log_text = context.get("log_text", "")
        return "kubectl" in log_text or "kubernetes" in log_text.lower()

    def execute(self, context):
        log_text = context.get("log_text", "")
        # Your custom K8s log analysis logic
        findings = analyze_k8s_logs(log_text)
        return {"k8s_findings": findings}
```

Load custom skills by setting `TAA_SKILLS_DIR=/path/to/my_skills` or via the API.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linter
ruff check src/ tests/

# Format code
ruff format src/ tests/
```

## License

MIT
