"""
modules/processor.py — RealityScan 実行エンジン

CLI コマンド構築、サブプロセス管理、リアルタイム進捗モニタリング、
REST/gRPC API 制御、停止制御を担当。
"""

import os
import shutil
import time
import glob
import queue
import threading
import subprocess
import requests

import gradio as gr

from config import (
    REALITYSCAN_PATH, UPLOAD_DIR, OUTPUT_DIR,
    CRASH_LOG_DIR, PROGRESS_DIR,
    QUALITY_OPTIONS, RS_STEP_NAMES,
    REST_API_ENABLED, REST_API_URL,
)
from modules.utils import (
    safe_filename, find_new_file, extract_frames,
    auto_adjust_texture_count, rotate_and_pack_glb,
    parse_realityscan_progress,
)
from modules.gs_handler import run_fastgs_backend


# ──────────────────────────────────────────────
# プロセス追跡（停止ボタン用）
# ──────────────────────────────────────────────

_active_process = None
_stop_requested = False


def stop_processing():
    """実行中のRealityScan/FastGSプロセスをすべて停止する"""
    global _active_process, _stop_requested
    _stop_requested = True
    results = []

    # 1. 追跡中のサブプロセスを停止
    if _active_process and _active_process.poll() is None:
        try:
            _active_process.terminate()
            _active_process.wait(timeout=5)
            results.append("サブプロセスを終了しました")
        except Exception:
            _active_process.kill()
            results.append("サブプロセスを強制終了しました")

    # 2. RealityScan プロセスを停止
    try:
        import psutil
        for proc in psutil.process_iter(['name']):
            if 'RealityScan' in (proc.info['name'] or ''):
                proc.terminate()
                results.append(f"RealityScan (PID:{proc.pid}) を終了しました")
    except ImportError:
        ret = subprocess.run(
            ['taskkill', '/F', '/IM', 'RealityScan.exe'],
            capture_output=True, text=True
        )
        if ret.returncode == 0:
            results.append("RealityScan を終了しました")
    except Exception as e:
        results.append(f"RealityScan 終了エラー: {e}")

    # 3. FastGS Docker コンテナを停止
    try:
        docker_ret = subprocess.run(
            ['docker', 'ps', '-q', '--filter', 'ancestor=realityscanwebui-fastgs'],
            capture_output=True, text=True, timeout=5
        )
        container_ids = docker_ret.stdout.strip().split()
        for cid in container_ids:
            if cid:
                subprocess.run(['docker', 'stop', cid], capture_output=True, timeout=15)
                results.append(f"Docker コンテナ {cid[:12]} を停止しました")
    except Exception as e:
        results.append(f"Docker 停止エラー: {e}")

    _active_process = None
    _stop_requested = False

    if not results:
        return "停止するプロセスはありませんでした"
    return "⏹ 処理を停止しました\n\n" + "\n".join(results)


# ──────────────────────────────────────────────
# REST/gRPC API ヘルパー
# ──────────────────────────────────────────────

def rest_api_send(command, params=None):
    """RealityScan REST API にコマンドを送信（Remote Command Plugin）

    REST_API_ENABLED=True の場合のみ動作。
    RealityScan 2.1 の Remote Command Plugin が起動している必要がある。
    """
    if not REST_API_ENABLED:
        return None
    try:
        url = f"{REST_API_URL}/v1/command"
        payload = {"command": command}
        if params:
            payload["params"] = params
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}


def rest_api_get_progress():
    """REST API から RealityScan の処理進捗を取得"""
    if not REST_API_ENABLED:
        return None
    try:
        response = requests.get(f"{REST_API_URL}/v1/progress", timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


# ──────────────────────────────────────────────
# メイン変換処理
# ──────────────────────────────────────────────

def convert_to_3d(files, project_name, quality,
                  simplify_enabled, simplify_count,
                  smooth_enabled, texture_max_count,
                  sampling_fps, ai_masking_enabled, wide_area_enabled,
                  run_3dgs_enabled,
                  progress=gr.Progress()):
    """3Dモデル変換（ジェネレーター：進捗をyield）

    RealityScan 2.1 広域スキャン対応版
    """

    if not files:
        yield "ファイルを選択してください"
        return
    if not project_name.strip():
        project_name = "model"

    safe_name = safe_filename(project_name)

    # 初期化
    progress(0.0, desc="初期化中...")
    shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ステップ1: ファイル準備 (0-10%)
    image_count = 0
    for i, file in enumerate(files):
        ext = os.path.splitext(file.name)[1].lower()
        if ext in ['.mp4', '.mov', '.avi', '.m4v']:
            progress((i / len(files)) * 0.10, desc=f"フレーム抽出中（{sampling_fps} fps）...")
            yield f"[1/3] フレーム抽出中... ({os.path.basename(file.name)}) @ {sampling_fps} fps"
            count = extract_frames(file.name, UPLOAD_DIR, fps=sampling_fps)
            image_count += count
            yield f"[1/3] フレーム抽出完了：{count}枚"
        elif ext in ['.jpg', '.jpeg', '.png', '.heic']:
            shutil.copy(file.name, UPLOAD_DIR)
            image_count += 1

    # VRAM/RAM 最適化: 広域モード時はテクスチャ枚数を自動調整
    effective_tex_count = int(texture_max_count)
    if wide_area_enabled:
        recommended = auto_adjust_texture_count(image_count, vram_gb=12)
        if effective_tex_count > recommended:
            effective_tex_count = recommended
            yield f"⚠ 広域モード: VRAM最適化のためテクスチャ枚数を {recommended} 枚に自動調整しました"

    # ステップ2: RealityScan実行 (10-90%)
    progress(0.10, desc="RealityScan実行中...")

    parts = [f"品質: {quality}", f"FPS: {sampling_fps}"]
    if ai_masking_enabled:
        parts.append("AIマスキング: ON")
    if wide_area_enabled:
        parts.append("広域モード: ON")
    if simplify_enabled:
        parts.append(f"簡略化: {int(simplify_count):,}ポリゴン")
    if smooth_enabled:
        parts.append("スムージング: ON")
    parts.append(f"テクスチャ: 最大{effective_tex_count}枚")

    yield (f"[2/3] RealityScan実行中... ({image_count}枚の画像)\n"
           + " | ".join(parts)
           + "\nしばらくお待ちください（大規模処理の場合は数十分かかることがあります）")

    before_time = time.time()

    # ===========================================================
    # CLI コマンド構築 (RealityScan 2.1)
    # ===========================================================
    quality_flag = QUALITY_OPTIONS.get(quality, "-calculateNormalModel")
    glb_output_path = os.path.join(OUTPUT_DIR, f"{safe_name}.glb")
    sparse_ply_output_path = os.path.join(OUTPUT_DIR, f"{safe_name}_realityscan_sparse.ply")
    progress_file = os.path.join(PROGRESS_DIR, f"{safe_name}_progress.txt")

    # 古い進捗ファイルを削除
    if os.path.exists(progress_file):
        os.remove(progress_file)

    # --- ヘッドレス + クラッシュ制御 + 進捗出力 (2.1 新機能) ---
    cmd = [
        REALITYSCAN_PATH,
        "-headless",
        "-silent", CRASH_LOG_DIR,
        "-stdConsole",
        "-writeProgress", progress_file, "2",
    ]

    # --- 画像追加 ---
    cmd += ["-addFolder", UPLOAD_DIR]

    # --- AIマスキング (2.1 新機能: -generateAIMasks) ---
    if ai_masking_enabled:
        cmd.append("-generateAIMasks")

    # --- アライメント ---
    cmd.append("-align")
    cmd.append("-setReconstructionRegionAuto")

    # --- 広域モード: コンポーネント結合（アライメント操作 — メッシュ生成前に実行） ---
    if wide_area_enabled:
        cmd.append("-mergeComponents")

    # --- 最大コンポーネント選択（メッシュ生成前に実行 — 複数コンポーネント時に必要） ---
    cmd.append("-selectMaximalComponent")

    # --- メッシュ生成（生成後はモデルが自動選択される） ---
    cmd.append(quality_flag)

    # --- 広域モード: 穴埋め（メッシュ操作 — メッシュ生成後に実行） ---
    if wide_area_enabled:
        cmd.append("-closeHoles")

    if simplify_enabled:
        cmd += ["-simplify", str(int(simplify_count))]
    if smooth_enabled:
        cmd.append("-smooth")

    # --- 処理後のモデルを命名 ---
    cmd += ["-renameSelectedModel", "output_model"]

    # --- テクスチャ設定 ---
    cmd += ["-set", f"unwrapMaximalTexCount={effective_tex_count}"]
    if wide_area_enabled:
        cmd += ["-set", "unwrapStyle=adaptive"]
    cmd.append("-calculateTexture")

    # --- エクスポート: GLB ---
    cmd += ["-exportModel", "output_model", glb_output_path]

    # --- エクスポート: Sparse Point Cloud ---
    cmd += ["-exportSparsePointCloud", sparse_ply_output_path]

    cmd.append("-quit")

    # ===========================================================
    # サブプロセス実行 + リアルタイム進捗モニタリング
    # ===========================================================
    try:
        global _active_process, _stop_requested
        _stop_requested = False
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        _active_process = process

        # --- stdout を非同期で読み取るスレッド ---
        stdout_lines = []
        stdout_queue = queue.Queue()

        def _read_stdout(pipe, q):
            for line in iter(pipe.readline, b''):
                decoded = line.decode('utf-8', errors='ignore').rstrip()
                if decoded:
                    q.put(decoded)
            pipe.close()

        reader_thread = threading.Thread(
            target=_read_stdout, args=(process.stdout, stdout_queue), daemon=True
        )
        reader_thread.start()

        while process.poll() is None:
            # 停止要求チェック
            if _stop_requested:
                yield "⏹ 停止要求を受信しました..."
                break

            # stdout キューからログ行を取得
            while not stdout_queue.empty():
                try:
                    stdout_lines.append(stdout_queue.get_nowait())
                except queue.Empty:
                    break

            # 進捗情報の構築
            elapsed = time.time() - before_time
            elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60):02d}秒"

            rs_prog = parse_realityscan_progress(progress_file)

            # --- stdoutログからフェーズを検出（-writeProgress が更新されない区間用） ---
            stdout_phase = None
            for line in reversed(stdout_lines[-30:]):
                if "Executing command" in line:
                    if "exportModel" in line:
                        stdout_phase = "GLBエクスポート中"
                    elif "exportSparsePointCloud" in line:
                        stdout_phase = "スパース点群エクスポート中"
                    elif "exportRegistration" in line:
                        stdout_phase = "COLMAPカメラデータ エクスポート中"
                    elif "exportUndistortedImages" in line:
                        stdout_phase = "歪み補正画像エクスポート中（時間がかかります）"
                    elif "exportMapsAndMask" in line:
                        stdout_phase = "深度/法線マップ エクスポート中"
                    elif "calculateTexture" in line:
                        stdout_phase = "テクスチャ生成中"
                    elif "closeHoles" in line:
                        stdout_phase = "穴埋め処理中"
                    elif "simplify" in line:
                        stdout_phase = "メッシュ簡略化中"
                    elif "smooth" in line:
                        stdout_phase = "スムージング中"
                    elif "selectMaximalComponent" in line:
                        stdout_phase = "最大コンポーネント選択中"
                    elif "mergeComponents" in line:
                        stdout_phase = "コンポーネント結合中"
                    break
                elif "Texturing Model completed" in line:
                    stdout_phase = "テクスチャ生成完了"
                    break
                elif "Exporting" in line and "completed" in line:
                    stdout_phase = "エクスポート処理中"
                    break
                elif "Reconstruction in" in line and "completed" in line:
                    stdout_phase = "メッシュ生成完了"
                    break

            if rs_prog and rs_prog.get("progress", -1) >= 0:
                pct = rs_prog["progress"]
                raw_name = rs_prog.get("name", "処理中")
                jp_name = RS_STEP_NAMES.get(raw_name, raw_name)
                pct_display = f"{pct * 100:.1f}%"

                bar_width = 30
                filled = int(bar_width * pct)
                bar = "█" * filled + "░" * (bar_width - filled)
            else:
                pct_display = "..."
                jp_name = "初期化中"
                bar = "░" * 30

            # stdoutから検出したフェーズがあればそちらを優先表示
            if stdout_phase:
                jp_name = stdout_phase

            # 最新のコンソールログ（末尾12行を常に表示）
            recent_logs = stdout_lines[-12:] if stdout_lines else ["(出力待機中...)"]
            log_block = "\n".join(f"  {l}" for l in recent_logs)

            # --- 毎回 yield して常にテキストを更新 ---
            msg = (
                f"[2/3] RealityScan 実行中\n"
                f"\n"
                f"  {bar}  {pct_display}\n"
                f"  フェーズ: {jp_name}\n"
                f"  経過時間: {elapsed_str}  |  ログ行数: {len(stdout_lines)}\n"
                f"\n"
                f"─── コンソールログ (末尾) ───\n"
                f"{log_block}"
            )
            yield msg

            time.sleep(2)

        # プロセス完了後: スレッドから残りのログ行を回収
        reader_thread.join(timeout=5)
        while not stdout_queue.empty():
            try:
                stdout_lines.append(stdout_queue.get_nowait())
            except queue.Empty:
                break
        remaining_output = "\n".join(stdout_lines[-50:])
        returncode = process.returncode

        if returncode != 0 and not os.path.exists(glb_output_path):
            yield f"RealityScan エラー (exit code: {returncode})\n\n{remaining_output[-500:]}"
            return

        progress(0.90, desc="出力確認中...")

        # GLB 出力確認
        expected = glb_output_path
        if not os.path.exists(expected):
            actual = find_new_file(OUTPUT_DIR, "glb", before_time)
            if actual and actual != expected:
                shutil.move(actual, expected)

        if not os.path.exists(expected):
            yield f"GLBファイルが見つかりません。\nRealityScan出力:\n{remaining_output[-500:]}"
            return

        # ステップ3: GLB 回転修正＋テクスチャ埋め込み (90-95%)
        progress(0.92, desc="GLB修正中...")
        yield "[3/3] GLB ファイルの回転修正（X軸 -90°）・テクスチャ統合中..."
        rot_success, rot_msg = rotate_and_pack_glb(expected)
        if rot_success:
            yield f"✅ GLB修正完了: {rot_msg}"
        else:
            yield f"⚠ GLB修正失敗（詳細↓）:\n{rot_msg}"

        # PLY 出力確認
        ply_exists = os.path.exists(sparse_ply_output_path)
        if not ply_exists:
            actual_ply = find_new_file(OUTPUT_DIR, "ply", before_time)
            if actual_ply and actual_ply != sparse_ply_output_path:
                shutil.move(actual_ply, sparse_ply_output_path)
                ply_exists = True

        glb_size = os.path.getsize(expected) / (1024 * 1024)

        # COLMAP エクスポート結果確認（既存データがある場合のスキップモード判定）
        colmap_dir = os.path.join(OUTPUT_DIR, f"{safe_name}_colmap")
        colmap_sparse_dir = os.path.join(colmap_dir, "sparse", "0")
        colmap_images_dir = os.path.join(colmap_dir, "images")
        colmap_skip_ready = False
        if os.path.isdir(colmap_sparse_dir) and os.path.isdir(colmap_images_dir):
            colmap_files = glob.glob(os.path.join(colmap_sparse_dir, "*.txt"))
            colmap_images = glob.glob(os.path.join(colmap_images_dir, "*"))
            if len(colmap_files) >= 2 and len(colmap_images) > 0:
                colmap_skip_ready = True

        progress(1.0, desc="変換完了！")

        total_elapsed = time.time() - before_time
        total_min = int(total_elapsed // 60)
        total_sec = int(total_elapsed % 60)
        result_lines = [
            "--- 変換完了 ---",
            f"⏱ 変換時間: {total_min}分{total_sec:02d}秒",
            "",
            f"📦 GLB: {os.path.basename(expected)} ({glb_size:.1f} MB)",
        ]
        if rot_success:
            result_lines.append("  → X軸 -90度回転修正済み")
        if ply_exists:
            ply_size = os.path.getsize(sparse_ply_output_path) / (1024 * 1024)
            result_lines.append(f"📦 Sparse PLY: {os.path.basename(sparse_ply_output_path)} ({ply_size:.1f} MB)")

        if run_3dgs_enabled:
            result_lines.append("")
            if colmap_skip_ready:
                result_lines.append("🔥 3DGS (FastGS) バックグラウンド学習開始 — ⚡ COLMAPスキップモード")
            else:
                result_lines.append("🔥 3DGS (FastGS) バックグラウンド学習開始 — Docker COLMAP + 学習")
            result_lines.append("   学習状況は「3DGS / PLY ビューワー」タブから確認できます。")
            threading.Thread(target=run_fastgs_backend, args=(safe_name,), daemon=True).start()

        result_lines.append("")
        result_lines.append("ビューワーで確認後、送信先を選んでください")

        yield "\n".join(result_lines)

    except Exception as e:
        yield f"エラー: {str(e)}"


# ──────────────────────────────────────────────
# ビューワーロード
# ──────────────────────────────────────────────

def load_viewer(project_name):
    """変換完了後、ビューワーにファイルをロード"""
    safe_name = safe_filename(project_name.strip() if project_name.strip() else "model")
    glb_path = os.path.join(OUTPUT_DIR, f"{safe_name}.glb")
    glb_out = glb_path if os.path.exists(glb_path) else None
    return glb_out, glb_out
