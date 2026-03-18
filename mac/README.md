# Interactive Feedback MCP — macOS

[Windows 版说明](../README_zh.md) · [English (Windows)](../README.md)

原项目 [interactive-feedback-mcp](https://github.com/junanchn/interactive-feedback-mcp) 的 macOS 移植，使用 Python + tkinter 实现，行为对齐 Windows 版。

## 环境要求

- macOS
- Python 3.9+
- Cursor 支持 MCP

## 安装

```bash
cd mac
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

安装后得到两个命令：

- `feedback-server-mac` — Cursor 挂载的 MCP 服务端
- `feedback-mac` — 独立命令行弹窗工具

## 配置 MCP

编辑 `~/.cursor/mcp.json`，在 `mcpServers` 下添加：

```json
"interactive-feedback-mcp": {
  "type": "stdio",
  "command": "<项目路径>/mac/.venv/bin/feedback-server-mac",
  "timeout": 900,
  "autoApprove": ["interactive_feedback"],
  "env": {
    "INTERACTIVE_FEEDBACK_HOME": "<项目路径>/mac/runtime"
  }
}
```

将 `<项目路径>` 替换为你 clone 仓库的实际路径。

Cursor Rules 配置与 Windows 版相同，参见 [主 README](../README_zh.md#3-配置-cursor-rules)。

## 使用

与 Windows 版一致：

- AI 回复后弹出反馈窗口
- 输入反馈 → `Cmd+Enter` 提交 → AI 在同一请求内继续
- 关闭窗口 / 提交空内容 → AI 结束请求
- 粘贴文件路径或 `file://` URL 自动转为路径插入
- 可选装 `tkinterdnd2` 获得文件拖拽支持

## 自动回复

在 `INTERACTIVE_FEEDBACK_HOME` 目录下创建规则文件，格式 `秒数|回复内容`：

- `auto_reply_oneshot.txt` — 按顺序逐条使用，用完即删
- `auto_reply_loop.txt` — 循环使用，用户提交非空反馈后重置

行为与 Windows 版一致，参见 [主 README](../README_zh.md#自动回复)。

## 命令行

```bash
feedback-mac "请确认改动方向" 600
```

## 测试

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## 与 Windows 版的差异

| | Windows | macOS |
|---|---------|-------|
| GUI | Win32 原生 | tkinter |
| 文件拖拽 | 原生支持 | 需安装 `tkinterdnd2` |
| IPC | Win32 消息 + 临时文件 | 临时目录 JSON 轮询 |
| 配置目录 | 服务端同目录 | `INTERACTIVE_FEEDBACK_HOME` → `project_directory` → 服务端同目录 |

## 许可证

MIT
