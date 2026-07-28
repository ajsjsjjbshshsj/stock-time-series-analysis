import subprocess
import sys
import os

# 获取项目根目录（当前脚本所在目录）
project_root = os.path.dirname(os.path.abspath(__file__))
venv_python = os.path.join(project_root, ".venv", "Scripts", "python.exe")
log_file = os.path.join(project_root, "run_log.txt")

os.chdir(project_root)

result = subprocess.run(
    [venv_python, "-c",
     "import sys; print(f'Python: {sys.executable}'); print(f'Version: {sys.version}')"],
    capture_output=True, text=True, cwd=project_root
)
with open(log_file, "w", encoding="utf-8") as f:
    f.write("STDOUT:\n" + result.stdout + "\n")
    if result.stderr:
        f.write("STDERR:\n" + result.stderr + "\n")
    f.write(f"Return code: {result.returncode}\n")
