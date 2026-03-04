import gradio as gr
import subprocess
import os
import shutil
import requests
import json
import re
import time
import math
import glob

# ===== 設定 =====

def _find_realityscan():
    """RealityScan 実行ファイルを自動検出（2.1 優先）"""
    candidates = [
        r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe",
        r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe",
        r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

REALITYSCAN_PATH = _find_realityscan()
FFMPEG_PATH = r"D:\ffmpeg\bin\ffmpeg.exe"
UPLOAD_DIR = r"D:\RealityScanWebUI\uploads"
OUTPUT_DIR = r"D:\RealityScanWebUI\output"
LOG_DIR = r"D:\RealityScanWebUI\logs"
CRASH_LOG_DIR = os.path.join(LOG_DIR, "crash")
PROGRESS_DIR = os.path.join(LOG_DIR, "progress")
UNITY_ASSETS_DIR = r"D:\RealityScan_unity\My project\Assets\ScannedModels"

# ディレクトリ自動作成
for _d in [LOG_DIR, CRASH_LOG_DIR, PROGRESS_DIR]:
    os.makedirs(_d, exist_ok=True)

# PlayCanvas設定
PLAYCANVAS_API_TOKEN = "zqeWLVUT18uCWH2uW3J0Dsl1N0oweD7w"
PLAYCANVAS_PROJECT_ID = "1466228"
PLAYCANVAS_SCENE_ID = "2422772"

# REST/gRPC API 設定 (RealityScan 2.1 Remote Command Plugin)
# 有効にすると CLI の代わりに REST API でリモート制御する。
# RealityScan を常駐起動し、Remote Command Plugin を有効化しておく必要がある。
REST_API_ENABLED = False
REST_API_URL = "http://localhost:20180"  # RealityScan REST API デフォルトポート

# 品質設定
QUALITY_OPTIONS = {
    "プレビュー（最速）": "-calculatePreviewModel",
    "ノーマル（バランス）": "-calculateNormalModel",
    "高品質（低速）": "-calculateHighModel",
}


# ===== ヘルパー関数 =====

def extract_frames(video_path, output_dir, fps=2.0):
    """動画からフレームを抽出（可変FPS対応）"""
    cmd = [
        FFMPEG_PATH, "-i", video_path,
        "-vf", f"fps={fps}", "-q:v", "2",
        os.path.join(output_dir, "frame_%04d.jpg"), "-y"
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return len([f for f in os.listdir(output_dir) if f.endswith('.jpg')])


def auto_adjust_texture_count(image_count, vram_gb=12):
    """VRAM/RAM制約に基づきテクスチャ枚数の推奨値を自動計算

    Args:
        image_count: 入力画像枚数
        vram_gb: 利用可能なVRAM(GB)

    Returns:
        推奨テクスチャ枚数
    """
    if vram_gb <= 8:
        base_max = 4
    elif vram_gb <= 12:
        base_max = 8
    else:
        base_max = 16

    if image_count > 1000:
        return min(base_max, 2)
    elif image_count > 500:
        return min(base_max, 4)
    else:
        return min(base_max, 8)


def rotate_and_pack_glb(glb_path):
    """GLBファイルのX軸-90度回転＋外部テクスチャをバイナリバッファに統合

    1. ビューワー/Blenderで真上から見た状態になる問題を修正する。
    2. 外部テクスチャPNGがあればGLBバイナリバッファに正しく埋め込み、
       Gradio等の単一ファイルビューワーでもテクスチャが表示されるようにする。
    """
    try:
        from pygltflib import GLTF2, BufferView
    except ImportError:
        return False, "pygltflib がインストールされていません。pip install pygltflib を実行してください。"

    try:
        gltf = GLTF2().load(glb_path)

        # --- 回転修正 ---
        angle = math.radians(-90)
        qx = math.sin(angle / 2)
        qw = math.cos(angle / 2)
        rotation = [qx, 0.0, 0.0, qw]

        scene = gltf.scenes[gltf.scene]
        for node_idx in scene.nodes:
            node = gltf.nodes[node_idx]
            node.rotation = rotation

        # --- テクスチャ埋め込み (手動バッファ操作) ---
        glb_dir = os.path.dirname(glb_path)
        embedded_count = 0

        for image in (gltf.images or []):
            if image.uri and not image.uri.startswith("data:"):
                img_path = os.path.join(glb_dir, image.uri)
                if os.path.exists(img_path):
                    with open(img_path, "rb") as f:
                        img_data = f.read()

                    # バイナリブロブ(blob_data)にテクスチャバイトを追加
                    blob = gltf.binary_blob()
                    if blob is None:
                        blob = b""
                    offset = len(blob)
                    blob += img_data

                    # バッファサイズを更新
                    if len(gltf.buffers) == 0:
                        from pygltflib import Buffer
                        gltf.buffers.append(Buffer(byteLength=len(blob)))
                    else:
                        gltf.buffers[0].byteLength = len(blob)

                    # 新しい bufferView を作成
                    bv_index = len(gltf.bufferViews)
                    gltf.bufferViews.append(BufferView(
                        buffer=0,
                        byteOffset=offset,
                        byteLength=len(img_data),
                    ))

                    # image を bufferView 参照に切り替え
                    ext = os.path.splitext(image.uri)[1].lower()
                    image.mimeType = "image/jpeg" if ext in ['.jpg', '.jpeg'] else "image/png"
                    image.bufferView = bv_index
                    image.uri = None  # 外部参照を削除

                    # バイナリブロブを書き戻し
                    gltf.set_binary_blob(blob)
                    embedded_count += 1

        if embedded_count > 0:
            tex_msg = f"回転修正＋テクスチャ{embedded_count}枚埋め込み完了"
        else:
            tex_msg = "回転修正完了"

        gltf.save(glb_path)
        return True, tex_msg
    except Exception as e:
        return False, f"GLB修正エラー: {str(e)}"


def safe_filename(name):
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
    return safe if len(safe) >= 2 else "model_" + safe


def find_new_file(output_dir, extension, before_time):
    found = glob.glob(os.path.join(output_dir, f"*.{extension}"))
    new_files = [f for f in found if os.path.getmtime(f) > before_time]
    return max(new_files, key=os.path.getmtime) if new_files else None


def upload_to_playcanvas(glb_path, model_name):
    headers = {"Authorization": f"Bearer {PLAYCANVAS_API_TOKEN}"}

    with open(glb_path, 'rb') as f:
        files = {'file': (f"{model_name}.glb", f, 'model/gltf-binary')}
        data = {'name': model_name, 'projectId': PLAYCANVAS_PROJECT_ID, 'preload': 'true'}
        response = requests.post(
            "https://playcanvas.com/api/assets",
            headers=headers, files=files, data=data
        )

    if response.status_code not in [200, 201]:
        return False, f"アップロード失敗: {response.text}"

    asset_data = response.json()
    asset_id = asset_data.get('id')
    if not asset_id:
        return False, "アセットIDが取得できませんでした"

    entity_data = {
        "name": model_name,
        "components": {"render": {"type": "asset", "asset": asset_id}},
        "position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]
    }
    response2 = requests.post(
        f"https://playcanvas.com/api/scenes/{PLAYCANVAS_SCENE_ID}/entities",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps(entity_data)
    )
    if response2.status_code in [200, 201]:
        return True, asset_id
    return True, f"アセットID:{asset_id}（シーン配置は手動で確認）"


def parse_fastgs_log(log_path):
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

    # FastGS 学習イテレーション解析 (train.pyの出力パターン)
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
        # 学習中: 20%〜90% をイテレーションで按分
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


def format_progress_bar(pct, width=30):
    """テキストベースのプログレスバーを生成"""
    if pct < 0:
        return f"{'━' * width}  ❌ ERROR"
    filled = int(width * pct / 100)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"{bar}  {pct}%"


def parse_realityscan_progress(progress_file):
    """RealityScan の -writeProgress 出力ファイルを解析して進捗を返す

    Returns:
        dict: {"name": str, "progress": float(0-1)} or None
    """
    if not os.path.exists(progress_file):
        return None
    try:
        with open(progress_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read().strip()
        if not content:
            return None

        lines = content.strip().splitlines()
        last_line = lines[-1] if lines else ""

        # XML 形式: <Progress name="Alignment" progress="0.35" .../>
        name_match = re.search(r'name="([^"]*)"', last_line)
        progress_match = re.search(r'progress="([^"]*)"', last_line)
        if progress_match:
            pct = float(progress_match.group(1))
            name = name_match.group(1) if name_match else "処理中"
            return {"name": name, "progress": pct}

        # テキスト形式: "processName progress 0.45" or "0.45"
        pct_match = re.search(r'(\d+\.?\d*)\s*%', last_line)
        if pct_match:
            pct = float(pct_match.group(1)) / 100.0
            return {"name": "処理中", "progress": min(pct, 1.0)}

        # フォールバック: 0.0-1.0 の浮動小数点値
        float_match = re.search(r'\b(0\.\d+|1\.0)\b', last_line)
        if float_match:
            return {"name": "処理中", "progress": float(float_match.group(1))}

        return {"name": last_line[:60], "progress": -1}
    except Exception:
        return None


# ===== REST/gRPC API ヘルパー =====

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
            f"✅ 学習完了！\n"
            f"出力: {os.path.basename(ply_path)} ({size:.1f} MB)\n"
            f"下の SuperSplat ビューワーにドラッグ＆ドロップして確認してください。"
        )
        return status, ply_path

    # ログ解析
    parsed = parse_fastgs_log(log_path)
    if parsed is None:
        return "⏸ 学習待機中、または実行されていません。", None

    info = parsed
    bar = format_progress_bar(info["progress_pct"])

    # ステップ表示を組み立て
    step_lines = []
    for s in info["steps"]:
        sid = s["id"]
        if info["is_error"] and sid == info["current_step"]:
            icon = "❌"
            suffix = ""
        elif sid < info["current_step"]:
            icon = "✅"
            suffix = ""
        elif sid == info["current_step"]:
            icon = "🔄"
            if sid == info.get("train_step", 3) and info["iteration"] > 0:
                suffix = f'  (iteration {info["iteration"]:,} / {info["max_iteration"]:,})'
            else:
                suffix = "  ..."
        else:
            icon = "⬜"
            suffix = ""
        step_lines.append(f"  {icon} {s['marker']} {s['label']}{suffix}")

    steps_block = "\n".join(step_lines)

    # 最新ログ（末尾）
    log_block = "\n".join(info["recent_log"])

    if info["is_error"]:
        header = "❌ エラーが発生しました"
    elif info["is_complete"]:
        header = "✅ 処理完了"
    elif info["is_skip_mode"]:
        header = "⚡ COLMAPスキップモード — 学習実行中..."
    else:
        header = "⏳ 学習実行中..."

    status = (
        f"{bar}\n"
        f"{header}\n\n"
        f"📋 ステップ:\n{steps_block}\n\n"
        f"{'─' * 40}\n"
        f"📝 ログ (最新):\n{log_block}"
    )
    return status, None


import threading
def run_fastgs_backend(project_name):
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
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        for line in iter(process.stdout.readline, b''):
            decoded_line = line.decode('utf-8', errors='ignore')
            f.write(decoded_line)
            f.flush()
        process.wait()


# ===== メイン処理 =====

def convert_to_3d(files, project_name, quality,
                  simplify_enabled, simplify_count,
                  smooth_enabled, texture_max_count,
                  sampling_fps, ai_masking_enabled, wide_area_enabled,
                  run_3dgs_enabled,
                  progress=gr.Progress()):
    """3Dモデル変換（ジェネレーター：進捗をyield）

    RealityScan 2.0 広域スキャン対応版
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
        "-silentcrashReportPath", CRASH_LOG_DIR,
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

    # --- 広域モード: コンポーネント結合 ---
    if wide_area_enabled:
        cmd.append("-mergeComponents")
        cmd.append("-closeHoles")

    # --- メッシュ生成 ---
    cmd.append(quality_flag)
    cmd += ["-renameSelectedModel", "output_model"]

    if simplify_enabled:
        cmd += ["-simplify", str(int(simplify_count))]
    if smooth_enabled:
        cmd.append("-smooth")

    # --- テクスチャ設定 ---
    cmd += ["-set", f"unwrapMaximalTexCount={effective_tex_count}"]
    if wide_area_enabled:
        cmd += ["-set", "unwrapStyle=adaptive"]
    cmd.append("-calculateTexture")

    # --- エクスポート: GLB ---
    cmd += ["-exportModel", "output_model", glb_output_path]

    # --- エクスポート: Sparse Point Cloud ---
    cmd += ["-exportSparsePointCloud", sparse_ply_output_path]

    # --- エクスポート: COLMAP 形式 (2.1 新機能 — COLMAPスキップモード) ---
    colmap_exported = False
    if run_3dgs_enabled:
        colmap_dir = os.path.join(OUTPUT_DIR, f"{safe_name}_colmap")
        colmap_sparse_dir = os.path.join(colmap_dir, "sparse", "0")
        colmap_images_dir = os.path.join(colmap_dir, "images")
        os.makedirs(colmap_sparse_dir, exist_ok=True)
        os.makedirs(colmap_images_dir, exist_ok=True)

        # カメラ登録情報 (cameras.txt, images.txt, points3D.txt)
        registration_path = os.path.join(colmap_sparse_dir, "cameras.txt")
        cmd += ["-exportRegistration", registration_path]

        # 歪み補正済み画像
        cmd += ["-exportUndistortedImages", colmap_images_dir]

        # 深度マップ・法線マップ (3DGS 学習補助データ)
        maps_dir = os.path.join(OUTPUT_DIR, f"{safe_name}_maps")
        os.makedirs(maps_dir, exist_ok=True)
        cmd += ["-exportMapsAndMask", maps_dir]

        colmap_exported = True

    cmd.append("-quit")

    # ===========================================================
    # サブプロセス実行 + リアルタイム進捗モニタリング
    # ===========================================================
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )

        last_yield_msg = ""
        rs_step_names = {
            "Feature detection": "特徴点検出",
            "Matching": "マッチング",
            "Alignment": "アライメント",
            "Depth map computation": "深度マップ計算",
            "Normal model": "メッシュ生成",
            "Preview model": "プレビューメッシュ生成",
            "High model": "高品質メッシュ生成",
            "Model computation": "メッシュ計算",
            "Unwrap": "UV展開",
            "Texturing": "テクスチャ計算",
            "Coloring": "カラーリング",
            "Simplification": "メッシュ簡略化",
            "Smoothing": "スムージング",
            "Export": "エクスポート",
            "AI Masking": "AIマスキング",
        }

        while process.poll() is None:
            rs_prog = parse_realityscan_progress(progress_file)
            if rs_prog and rs_prog.get("progress", -1) >= 0:
                pct = rs_prog["progress"]
                raw_name = rs_prog.get("name", "処理中")
                jp_name = rs_step_names.get(raw_name, raw_name)
                pct_display = f"{pct * 100:.0f}%"
                msg = f"[2/3] RealityScan: {jp_name} ({pct_display})"
                if msg != last_yield_msg:
                    progress(0.10 + pct * 0.78, desc=f"RealityScan: {jp_name}")
                    yield msg
                    last_yield_msg = msg
            time.sleep(2)

        # プロセス完了後の出力取得
        remaining_output = process.stdout.read().decode(errors='ignore') if process.stdout else ""
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
        yield "[3/3] GLB ファイルの回転修正・テクスチャ統合中..."
        rot_success, rot_msg = rotate_and_pack_glb(expected)
        if not rot_success:
            yield f"⚠ GLB修正スキップ: {rot_msg}"

        # PLY 出力確認
        ply_exists = os.path.exists(sparse_ply_output_path)
        if not ply_exists:
            actual_ply = find_new_file(OUTPUT_DIR, "ply", before_time)
            if actual_ply and actual_ply != sparse_ply_output_path:
                shutil.move(actual_ply, sparse_ply_output_path)
                ply_exists = True

        glb_size = os.path.getsize(expected) / (1024 * 1024)

        # COLMAP エクスポート結果確認
        colmap_skip_ready = False
        if colmap_exported:
            colmap_files = glob.glob(os.path.join(colmap_sparse_dir, "*.txt"))
            colmap_images = glob.glob(os.path.join(colmap_images_dir, "*"))
            if len(colmap_files) >= 2 and len(colmap_images) > 0:
                colmap_skip_ready = True

        progress(1.0, desc="変換完了！")

        result_lines = [
            "--- 変換完了 ---",
            "",
            f"📦 GLB: {os.path.basename(expected)} ({glb_size:.1f} MB)",
        ]
        if rot_success:
            result_lines.append("  → X軸 -90度回転修正済み")
        if ply_exists:
            ply_size = os.path.getsize(sparse_ply_output_path) / (1024 * 1024)
            result_lines.append(f"📦 Sparse PLY: {os.path.basename(sparse_ply_output_path)} ({ply_size:.1f} MB)")

        if colmap_exported:
            if colmap_skip_ready:
                result_lines.append(f"📦 COLMAP: {colmap_sparse_dir} (✅ スキップモード準備完了)")
                result_lines.append(f"📦 歪み補正画像: {colmap_images_dir}")
            else:
                result_lines.append("⚠ COLMAPエクスポート: ファイルが不完全（Docker COLMAP フォールバック使用）")

            maps_files = glob.glob(os.path.join(maps_dir, "*")) if os.path.isdir(maps_dir) else []
            if maps_files:
                result_lines.append(f"📦 深度/法線マップ: {maps_dir} ({len(maps_files)} files)")

        if run_3dgs_enabled:
            result_lines.append("")
            if colmap_skip_ready:
                result_lines.append("🔥 3DGS (FastGS) バックグラウンド学習開始 — ⚡ COLMAPスキップモード")
            else:
                result_lines.append("🔥 3DGS (FastGS) バックグラウンド学習開始 — 従来モード (Docker COLMAP)")
            result_lines.append("   学習状況は「3DGS / PLY ビューワー」タブから確認できます。")
            threading.Thread(target=run_fastgs_backend, args=(safe_name,), daemon=True).start()

        result_lines.append("")
        result_lines.append("ビューワーで確認後、送信先を選んでください")

        yield "\n".join(result_lines)

    except Exception as e:
        yield f"エラー: {str(e)}"


def load_viewer(project_name):
    """変換完了後、ビューワーにファイルをロード"""
    safe_name = safe_filename(project_name.strip() if project_name.strip() else "model")
    glb_path = os.path.join(OUTPUT_DIR, f"{safe_name}.glb")
    glb_out = glb_path if os.path.exists(glb_path) else None
    return glb_out, glb_out


def upload_to_targets(glb_path, project_name, send_unity, send_playcanvas):
    """選択された送信先にファイルをアップロード"""
    if not project_name.strip():
        project_name = "model"

    if not send_unity and not send_playcanvas:
        return "送信先を少なくとも1つ選択してください"

    results = []

    if send_unity:
        if glb_path and os.path.exists(glb_path):
            project_dir = os.path.join(UNITY_ASSETS_DIR, project_name)
            os.makedirs(project_dir, exist_ok=True)
            # GLBコピー
            shutil.copy2(glb_path, os.path.join(project_dir, os.path.basename(glb_path)))
            # 付随するテクスチャファイル群もコピー
            base_name = os.path.splitext(os.path.basename(glb_path))[0]
            output_dir = os.path.dirname(glb_path)
            for f in os.listdir(output_dir):
                if f.startswith(base_name) and f.endswith(('.png', '.jpg', '.jpeg')):
                    shutil.copy2(os.path.join(output_dir, f), os.path.join(project_dir, f))
            
            results.append(f"Unity: {os.path.join(project_dir, os.path.basename(glb_path))} および関連テクスチャをコピーしました")
        else:
            results.append("Unity: GLBファイルが見つかりません")

    if send_playcanvas:
        if glb_path and os.path.exists(glb_path):
            success, result = upload_to_playcanvas(glb_path, project_name)
            if success:
                results.append(
                    f"PlayCanvas: アセットID {result}\n"
                    f"https://playcanvas.com/editor/scene/{PLAYCANVAS_SCENE_ID}"
                )
            else:
                results.append(f"PlayCanvas: {result}")
        else:
            results.append("PlayCanvas: GLBファイルがありません")

    return "--- 送信結果 ---\n\n" + "\n\n".join(results)


# ===== WebUI =====

with gr.Blocks(
    title="RealityScan/FastGS WebUI",
    theme=gr.themes.Soft(primary_hue="orange")
) as app:

    gr.Markdown("# RealityScan 2.1 / FastGS WebUI")
    gr.Markdown("写真・動画 → 3Dモデル(GLB)生成 → プレビュー → Unity / PlayCanvas へ送信")
    gr.Markdown(f"*RealityScan: `{os.path.basename(os.path.dirname(REALITYSCAN_PATH))}` | "
                f"ヘッドレスモード | REST/gRPC: {'✅ 有効' if REST_API_ENABLED else '⬜ 無効'}*")

    glb_state = gr.State(None)

    # ========== セクション1: 入力・設定 ==========
    file_input = gr.File(
        label="写真・動画をここにドロップ（複数選択可）",
        file_count="multiple",
        file_types=["image", "video", ".mov", ".mp4", ".m4v"]
    )

    with gr.Row():
        project_name_input = gr.Textbox(
            label="モデル名",
            placeholder="例: garden01",
            value="",
            scale=2
        )
        quality_input = gr.Dropdown(
            label="メッシュ品質",
            choices=list(QUALITY_OPTIONS.keys()),
            value="ノーマル（バランス）",
            scale=1
        )

    # --- RealityScan 2.1 新機能 & FastGS ---
    with gr.Row():
        run_3dgs_enabled = gr.Checkbox(
            label="🔥 FastGS (3DGS) 同時学習 + COLMAPスキップ (Docker必須)",
            value=True, scale=2
        )
        ai_masking_enabled = gr.Checkbox(
            label="🎭 AIマスキング（空・動体の自動除外）",
            value=False, scale=1
        )
        wide_area_enabled = gr.Checkbox(
            label="🌐 広域モード（コンポーネント結合 + 穴埋め）",
            value=False, scale=1
        )

    with gr.Accordion("詳細設定（FPS・簡略化・スムージング・テクスチャ）", open=False):
        gr.Markdown("#### 動画フレーム抽出")
        sampling_fps = gr.Slider(
            label="動画抽出 FPS（低い＝少ない画像/高速、高い＝多い画像/高精細）",
            minimum=0.5, maximum=10.0, value=2.0, step=0.5,
        )
        gr.Markdown("#### メッシュ設定")
        with gr.Row():
            simplify_enabled = gr.Checkbox(label="メッシュ簡略化", value=False, scale=1)
            simplify_count = gr.Slider(
                label="目標ポリゴン数",
                minimum=10000, maximum=5000000, value=200000, step=10000,
                scale=2
            )
        with gr.Row():
            smooth_enabled = gr.Checkbox(label="スムージング", value=False, scale=1)
            texture_max_count = gr.Slider(
                label="テクスチャ最大枚数（広域モード時は自動調整されます）",
                minimum=1, maximum=16, value=1, step=1,
                scale=2
            )

    with gr.Accordion("REST/gRPC API 設定 (上級者向け)", open=False):
        gr.Markdown(
            "RealityScan 2.1 の **Remote Command Plugin** を使用すると、"
            "CLI の代わりに REST/gRPC API でリモート制御できます。\n\n"
            "1. RealityScan を起動し、Remote Command Plugin を有効化\n"
            "2. 下記 URL を設定して「有効化」\n"
            "3. 変換実行時に REST API 経由で処理が行われます\n\n"
            "*現在は CLI モード（ヘッドレス）で動作しています。*"
        )
        with gr.Row():
            rest_api_url_input = gr.Textbox(
                label="REST API URL",
                value=REST_API_URL,
                placeholder="http://localhost:20180",
                scale=3
            )
            rest_api_toggle = gr.Checkbox(
                label="REST API を有効化",
                value=REST_API_ENABLED,
                scale=1
            )

    convert_btn = gr.Button("3Dモデル・3DGS変換を開始", variant="primary", size="lg")
    status_output = gr.Textbox(label="処理状況（リアルタイム進捗表示）", lines=8)

    # ========== セクション2: プレビュー ==========
    gr.Markdown("---")
    
    with gr.Tabs():
        with gr.Tab("GLB (ポリゴンメッシュ)"):
            gr.Markdown("RealityScan で生成されたテクスチャ付きポリゴンメッシュです。")
            glb_viewer = gr.Model3D(label="GLB ビューワー", height=480)
            
        with gr.Tab("3DGS / PLY ビューワー (FastGS)"):
            gr.Markdown("Docker で学習を実行中の 3DGS モデル（Splat形式PLY）の状態確認とプレビューを行います。")

            with gr.Row():
                check_status_btn = gr.Button("🔄 手動で更新", variant="secondary", scale=1)
                auto_refresh_enabled = gr.Checkbox(
                    label="⏱ 自動更新 (5秒間隔)",
                    value=False, scale=1
                )

            gs_status = gr.Textbox(
                label="3DGS (FastGS) 学習ステータス",
                lines=16,
                max_lines=20,
            )
            gs_ply_file = gr.File(label="学習済 Splat PLY", interactive=False)

            # 自動更新タイマー
            gs_timer = gr.Timer(value=5, active=False)

            gr.Markdown("### SuperSplat ビューワー")
            gr.Markdown("✅ 学習が完了し PLY ファイルが出力されたら、上のファイルを手元にダウンロードし、下のビューワーに **ドラッグ＆ドロップ** して閲覧してください。")
            gr.HTML("""
            <iframe src="https://superspl.at/editor" width="100%" height="600px" style="border: 1px solid #ccc; border-radius: 8px;"></iframe>
            """)

    # ========== セクション3: 送信先 ==========
    gr.Markdown("---")
    gr.Markdown("### 送信先")
    with gr.Row():
        send_unity = gr.Checkbox(label="Unity（GLB）", value=False)
        send_playcanvas = gr.Checkbox(label="PlayCanvas（GLB）", value=False)

    upload_btn = gr.Button("選択した送信先へアップロード", variant="secondary", size="lg")
    upload_status = gr.Textbox(label="送信結果", lines=5)

    # ========== イベントハンドラ ==========

    convert_btn.click(
        fn=convert_to_3d,
        inputs=[
            file_input, project_name_input, quality_input,
            simplify_enabled, simplify_count,
            smooth_enabled, texture_max_count,
            sampling_fps, ai_masking_enabled, wide_area_enabled,
            run_3dgs_enabled
        ],
        outputs=[status_output]
    ).then(
        fn=load_viewer,
        inputs=[project_name_input],
        outputs=[glb_viewer, glb_state]
    )

    check_status_btn.click(
        fn=check_fastgs_status,
        inputs=[project_name_input],
        outputs=[gs_status, gs_ply_file]
    )

    # 自動更新チェックボックス → タイマーON/OFF
    auto_refresh_enabled.change(
        fn=lambda enabled: gr.Timer(active=enabled),
        inputs=[auto_refresh_enabled],
        outputs=[gs_timer]
    )

    # タイマーによる自動ステータス更新
    gs_timer.tick(
        fn=check_fastgs_status,
        inputs=[project_name_input],
        outputs=[gs_status, gs_ply_file]
    )

    upload_btn.click(
        fn=upload_to_targets,
        inputs=[glb_state, project_name_input, send_unity, send_playcanvas],
        outputs=[upload_status]
    )

app.launch(server_name="0.0.0.0", server_port=7860, share=False)
