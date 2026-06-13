# Claude Insight

A **private, local, AI-powered** analysis tool for your Claude Code sessions. Understand how you build with AI — without your data ever leaving your machine.

## 🚀 What It Does

Claude Insight parses your local Claude Code transcript files and generates:

- **Builder Archetype** — Are you an Architect, Sprinter, Debugger, Collaborator, or Autonomous Agent?
- **Efficiency Score** — How effectively you steer AI coding tools
- **Prompt Quality Analysis** — Specificity, context, iteration patterns
- **Session Insights** — Duration, token usage, tool utilization
- **Growth Recommendations** — Concrete improvements based on your patterns

## 🔒 Privacy First

- **100% local** — No network calls, no uploads, no telemetry
- **Read-only** — Never modifies your Claude Code files
- **No API keys required** — Runs entirely offline
- **Open source** — MIT license, auditable

## 📦 Installation

### One-liner (recommended)
```bash
curl -fsSL https://raw.githubusercontent.com/Feloguarin/claude-insight/main/install.sh | bash
```

### Manual
```bash
# Clone the repository
git clone https://github.com/Feloguarin/claude-insight.git
cd claude-insight

# Install dependencies
pip install -r requirements.txt

# Run analysis
python -m claude_insight analyze ~/.claude/projects/
```

## 🎯 Usage

### Analyze All Sessions
```bash
python -m claude_insight analyze ~/.claude/projects/
```

### Analyze Specific Session
```bash
python -m claude_insight analyze ~/.claude/projects/session_abc123.jsonl
```

### Generate HTML Report
```bash
python -m claude_insight report ~/.claude/projects/ --output report.html
```

### View Raw Metrics (JSON)
```bash
python -m claude_insight metrics ~/.claude/projects/ --json
```

## 📊 Example Output

```
╔══════════════════════════════════════════╗
║     Your AI Builder Profile              ║
╠══════════════════════════════════════════╣
║  Archetype: 🏗️  Architect               ║
║  Efficiency Score: 78/100                ║
║  Sessions Analyzed: 42                   ║
║  Total Prompts: 1,247                  ║
║  Avg Prompt Length: 312 chars            ║
║  Top Tools: Read(45%), Edit(30%), Bash(15%) ║
╚══════════════════════════════════════════╝

🎯 Growth Edge:
   → Your planning prompts are strong, but try adding
     specific file paths to reduce ambiguity
   → Consider using more multi-step tool sequences
```

## 🏗️ Architecture

```
claude_insight/
├── parser/          # JSONL transcript parsing
├── analyzer/        # Metric computation & archetype detection
├── reports/         # HTML & terminal report generation
└── cli.py          # Command-line interface
```

## 📈 Metrics Computed

### 5 Dimensions of Building
1. **Steering** — How well you direct AI tools
2. **Execution** — Speed and iteration efficiency
3. **Engineering** — Code quality and systematic approach
4. **Product Instinct** — Focus on outcomes vs. process
5. **Planning** — Research-to-build ratio

### Archetypes
- **🏗️ Architect** — Plans extensively, low code churn
- **⚡ Sprinter** — High velocity, rapid iteration
- **🐛 Debugger** — Methodical problem-solving
- **🤝 Collaborator** — Seeks alignment, asks questions
- **🤖 Autonomous Agent** — Delegates end-to-end workflows

## 🔧 Development

```bash
# Run tests
pytest

# Run linting
ruff check .

# Type checking
mypy claude_insight/
```

## 📄 License

MIT License — see [LICENSE](LICENSE) file.

## 🙏 Acknowledgments

Inspired by Y Combinator's Paxel, but built for privacy-conscious developers who want their data to stay local.
