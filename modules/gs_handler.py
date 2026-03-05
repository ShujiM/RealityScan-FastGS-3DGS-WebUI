"""
modules/gs_handler.py — 3DGS (FastGS) 学習ハンドラ

Docker 経由のバックグラウンド学習、ログ解析、ステータス表示を担当。
"""

import os
import re
import subprocess
import threading

from config import OUTPUT_DIR, BASE_DIR
from modules.utils import safe_filename, format_progress_bar


# ──────────────────────────────────────────────
# FastGS ログ解析
# ──────────────────────────────────────────────

def parse_fastgs_log(log_path: str):
    """FastGSのログを解析してステップ・進捗・ログを返す"""
    if not os.path.exists(log_path):
        return None

    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        lines = content.splitlines()

    # COLMAPスキップモード判定
    is_skip_mode = "COLMAPスキップモード" in content

    # ステップ定義（モードで分岐）
    if is_skip_mode:
        steps = [
            {"id": 1, "marker": "[1/3]", "label": "RealityScan COLMAP データコピー"},
            {"id": 2, "marker": "[2/3]", "label": "FastGS 高速学習"},
            {"id": 3, "marker": "[3/3]", "label": "出力ファイル確認"},
        ]
    else:
        steps = [
            {"id": 1, "marker": "[1/4]", "label": "画像コピー"},
            {"id": 2, "marker": "[2/4]", "label": "COLMAP カメラポーズ推定 (SfM)"},
            {"id": 3, "marker": "[3/4]", "label": "FastGS 高速学習"},
            {"id": 4, "marker": "[4/4]", "label": "出力ファイル確認"},
        ]

    # 現在のステップを特定
    current_step = 0
    for step in steps:
        if step["marker"] in content:
            current_step = step["id"]

    # 完了判定
    is_complete = "=== 完了!" in content
    is_error = "ERROR:" in content

    # FastGS 学習イテレーション解析
    iteration = 0
    max_iteration = 30000
    iter_matches = re.findall(r'(?:ITER|iteration)\s*[\[:]?\s*(\d+)', content, re.IGNORECASE)
    if iter_matches:
        iteration = int(iter_matches[-1])

    # 全体進捗率の計算
    total_steps = len(steps)
    train_step = 2 if is_skip_mode else 3

    if is_complete:
        progress_pct = 100
    elif is_error:
        progress_pct = -1
    elif current_step < train_step:
        progress_pct = int(15 * current_step / train_step)
    elif current_step == train_step:
        progress_pct = 20 + int(70 * iteration / max_iteration)
    elif current_step > train_step:
        progress_pct = 95
    else:
        progress_pct = 0

    # 最新ログ行（空行除外）
    recent = [l for l in lines if l.strip()][-12:]

    return {
        "current_step": current_step,
        "steps": steps,
        "progress_pct": min(progress_pct, 100),
        "iteration": iteration,
        "max_iteration": max_iteration,
        "train_step": train_step,
        "is_complete": is_complete,
        "is_error": is_error,
        "is_skip_mode": is_skip_mode,
        "recent_log": recent,
        "total_lines": len(lines),
    }


# ──────────────────────────────────────────────
# ステータス表示
# ──────────────────────────────────────────────

def check_fastgs_status(project_name):
    """FastGSのログや出力PLYを確認する（プログレスバー付き）"""
    safe_name = safe_filename(project_name.strip() if project_name.strip() else "model")
    ply_path = os.path.join(OUTPUT_DIR, f"{safe_name}_3dgs.ply")
    log_path = os.path.join(OUTPUT_DIR, f"{safe_name}_fastgs.log")

    # 完了済み PLY チェック
    if os.path.exists(ply_path):
        size = os.path.getsize(ply_path) / (1024 * 1024)
        bar = format_progress_bar(100)
        status = (
            f"{bar}\n\n"
            f"学習完了！\n"
            f"出力: {os.path.basename(ply_path)} ({size:.1f} MB)\n"
            f"下の SuperSplat ビューワーにドラッグ＆ドロップして確認してください。"
        )
        return status, ply_path

    # ログ解析
    parsed = parse_fastgs_log(log_path)
    if parsed is None:
        return "学習待機中、または実行されていません。", None

    info = parsed
    bar = format_progress_bar(info["progress_pct"])

    # ステップ表示を組み立て
    step_lines = []
    for s in info["steps"]:
        sid = s["id"]
        if info["is_error"] and sid == info["current_step"]:
            icon = "[X]"
            suffix = ""
        elif sid < info["current_step"]:
            icon = "[v]"
            suffix = ""
        elif sid == info["current_step"]:
            icon = "[>]"
            if sid == info.get("train_step", 3) and info["iteration"] > 0:
                suffix = f'  (iteration {info["iteration"]:,} / {info["max_iteration"]:,})'
            else:
                suffix = "  ..."
        else:
            icon = "[ ]"
            suffix = ""
        step_lines.append(f"  {icon} {s['marker']} {s['label']}{suffix}")

    steps_block = "\n".join(step_lines)
    log_block = "\n".join(info["recent_log"])

    if info["is_error"]:
        header = "エラーが発生しました"
    elif info["is_complete"]:
        header = "処理完了"
    elif info["is_skip_mode"]:
        header = "COLMAPスキップモード — 学習実行中..."
    else:
        header = "学習実行中..."

    status = (
        f"{bar}\n"
        f"{header}\n\n"
        f"ステップ:\n{steps_block}\n\n"
        f"{'─' * 40}\n"
        f"ログ (最新):\n{log_block}"
    )
    return status, None


# ──────────────────────────────────────────────
# バックグラウンド学習実行
# ──────────────────────────────────────────────

def run_fastgs_backend(project_name: str):
    """DockerでFastGS学習をバックグラウンド実行"""
    safe_name = safe_filename(project_name)
    log_path = os.path.join(OUTPUT_DIR, f"{safe_name}_fastgs.log")

    cmd = [
        "docker-compose", "run", "--rm",
        "fastgs", "/workspace/scripts/run_speedysplat.sh", safe_name
    ]

    with open(log_path, 'w', encoding='utf-8') as f:
        f.write(f"=== Starting 3DGS Training for {safe_name} ===\n")
        f.flush()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=BASE_DIR
        )
        for line in iter(process.stdout.readline, b''):
            decoded_line = line.decode('utf-8', errors='ignore')
            f.write(decoded_line)
            f.flush()
        process.wait()
