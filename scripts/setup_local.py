"""Create ignored local editor configuration, preserving an existing .env."""
import json
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
if not env_file.exists():
    template = (root / ".env.example").read_text(encoding="utf-8")
    env_file.write_text(template.replace(
        "INVESTLAB_AI_SERVICE_TOKEN=\n",
        f"INVESTLAB_AI_SERVICE_TOKEN={secrets.token_urlsafe(40)}\n",
    ), encoding="utf-8")

editor = root / ".vscode"
editor.mkdir(exist_ok=True)
configs = {
    "settings.json": {
        "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
        "python.analysis.extraPaths": ["${workspaceFolder}/src"],
        "python.testing.pytestEnabled": True,
        "python.testing.pytestArgs": ["tests", "--basetemp", ".local/pytest-tmp"],
        "files.exclude": {"**/__pycache__": True},
    },
    "launch.json": {"version": "0.2.0", "configurations": [{
        "name": "InvestLab AI: Neon shared server", "type": "debugpy", "request": "launch",
        "program": "${workspaceFolder}/scripts/start_neon_chat.py",
        "python": "${workspaceFolder}/.venv/Scripts/python.exe",
        "args": ["--no-browser"], "subProcess": True,
        "cwd": "${workspaceFolder}", "console": "integratedTerminal", "justMyCode": True,
    }, {
        "name": "InvestLab AI: local server", "type": "debugpy", "request": "launch",
        "program": "${workspaceFolder}/scripts/start_chat.py",
        "python": "${workspaceFolder}/.venv/Scripts/python.exe",
        "args": ["--no-browser", "--port", "8000"], "subProcess": True,
        "cwd": "${workspaceFolder}", "envFile": "${workspaceFolder}/.env",
        "console": "integratedTerminal", "justMyCode": True,
    }]},
    "extensions.json": {"recommendations": ["ms-python.python", "ms-python.debugpy"]},
    "tasks.json": {"version": "2.0.0", "tasks": [
        {"label": "InvestLab: start server", "type": "process", "command": "powershell",
         "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/dev.ps1"],
         "options": {"cwd": "${workspaceFolder}"}, "problemMatcher": []},
        {"label": "InvestLab: tests", "type": "process",
         "command": "${workspaceFolder}/.venv/Scripts/python.exe",
         "args": ["-m", "pytest", "-q", "--basetemp", ".local/pytest-tmp"],
         "options": {"cwd": "${workspaceFolder}"}, "problemMatcher": []},
    ]},
}
for name, content in configs.items():
    target = editor / name
    if not target.exists():
        target.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("로컬 .env / VS Code 설정 준비 완료. 기존 설정은 덮어쓰지 않았습니다.")
