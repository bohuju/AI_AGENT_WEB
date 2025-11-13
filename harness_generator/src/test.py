from __future__ import annotations
from fuzz_unharnessed_repo import NonOssFuzzHarnessGenerator, RepoSpec
from pathlib import Path
import subprocess
from graphviz import Source

from typing import Dict, List, Optional, Sequence, Tuple
from dataclasses import dataclass
import tempfile
from git import Repo, exc as git_exc
import os
from pathlib import Path
from typing import Optional


def _clone_repo(spec: RepoSpec) -> Path:
    root = spec.workdir or Path(tempfile.mkdtemp(prefix="sherpa-fuzz-"))
    root = root.resolve()
    if root.exists() and any(root.iterdir()):
        # If provided, allow using an existing working folder (e.g., dev)
        print(f"[*] Using existing working directory: {root}")
        os.chdir(root)
        return root
    print(f"[*] Cloning {spec.url} → {root}")
    repo = Repo.clone_from(spec.url, root)
    if spec.ref:
        try:
            repo.git.checkout(spec.ref)
        except git_exc.GitCommandError:
            repo.git.fetch("origin", spec.ref)
            repo.git.checkout("FETCH_HEAD")
    print(f"[*] Checked out commit {repo.head.commit.hexsha}")
    os.chdir(root)
    return root

def _resolve_paths(input_dir: str, output_path: str) -> tuple[Path, Path, Path, Path]:
    """解析路径并返回 (static_analysis_dir, analyzer_bin, output_path_abs, input_dir_abs)。"""
    # static_analysis_dir = Path(__file__).resolve().parents[1]
    static_analysis_dir =   Path("/home/bohuju/AI-agent-for-Cyber-Security/Static-Analysis")
    analyzer_bin = static_analysis_dir / "analyzer"
    output_abs = (static_analysis_dir / output_path).resolve() if not os.path.isabs(output_path) else Path(output_path)
    input_dir_abs = (static_analysis_dir / input_dir).resolve() if not os.path.isabs(input_dir) else Path(input_dir)
    return static_analysis_dir, analyzer_bin, output_abs, input_dir_abs


def run_analyzer(input_dir: str, output_path: str) -> str:
    """调用本地 analyzer 生成调用图 DOT 文件，返回简单结果字符串。"""
    static_analysis_dir, analyzer_bin, output_abs, input_abs = _resolve_paths(input_dir, output_path)

    if not analyzer_bin.exists():
        raise FileNotFoundError(f"未找到 analyzer 可执行文件: {analyzer_bin}")
    if not os.access(analyzer_bin, os.X_OK):
        raise PermissionError(f"analyzer 不可执行: {analyzer_bin}")
    if not input_abs.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_abs}")

    cmd = [str(analyzer_bin), "-input", str(input_abs), "-output", str(output_abs)]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(static_analysis_dir),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"analyzer 执行失败，退出码 {e.returncode}:\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}"
        ) from e

    return (
        "Analyzer 执行成功。\n"
        f"命令: {' '.join(cmd)}\n"
        f"STDOUT:\n{proc.stdout}\n"
        f"输出文件: {output_abs}"
    )

if __name__ == "__main__":
    repospec = RepoSpec(
        url="https://github.com/syoyo/tinyexr.git"
    )
    generator = NonOssFuzzHarnessGenerator(
    repo_spec= repospec,
    ai_key_path=Path("./.env"),)
    generator.generate()
    # from pathlib import Path
    # dot_path = Path("/tmp/sherpa-fuzz-g80hbp40/output_fixed.dot")
    # lines = dot_path.read_text(encoding="utf-8").splitlines()

    # for i in range(max(0, 88 - 5), min(len(lines), 88 + 5)):
    #     print(f"{i+1:>4}: {lines[i]}")


    # import re
    # from pathlib import Path

    # # ======== 配置部分 ========
    # input_path = Path("/tmp/sherpa-fuzz-g80hbp40/output.dot")  
    # output_path = input_path.with_name("output_fixed.dot")
    # # ===========================

    # print(f"开始修复 {input_path} ...")

    # text = input_path.read_text(encoding="utf-8")

    # # --- Step 1: 修复多余的双引号开头，例如 ""OpenGL error...
    # text = re.sub(r'""([A-Za-z0-9_])', r'"\1', text)

    # # --- Step 2: 修复 label 字段内部的转义问题
    # def fix_label(match: re.Match):
    #     label = match.group(1)

    #     # 先统一转义反斜杠
    #     label = label.replace("\\", "\\\\")  # 单反斜杠改双反斜杠
    #     # 再转义引号
    #     label = label.replace('\\"', '\\\\\"')  # 修复 \" 的情况
    #     # 保留 \n 换行（变成 \\n）
    #     label = label.replace('\\\\n', '\\n')

    #     return f'label="{label}"'

    # text = re.sub(r'label="([^"]*)"', fix_label, text)

    # # --- Step 3: 确保文件结构正常
    # if not text.strip().endswith("}"):
    #     text += "\n}\n"

    # # --- Step 4: 写入修复后的文件
    # output_path.write_text(text, encoding="utf-8")
    # print(f"修复完成，已保存到 {output_path}")

    # print("现在可以尝试运行：")
    # print(f"  dot -Tpng {output_path} -o output.png")
    # import subprocess
    # subprocess.run(["dot", "-Tpng", str(output_path), "-o", str(output_path.with_suffix(".png"))])
    # print("已生成图片：", output_path.with_suffix(".png"))
