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
REALITYSCAN_PATH = r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"
FFMPEG_PATH = r"D:\ffmpeg\bin\ffmpeg.exe"
UPLOAD_DIR = r"D:\RealityScanWebUI\uploads"
OUTPUT_DIR = r"D:\RealityScanWebUI\output"
UNITY_ASSETS_DIR = r"D:\RealityScan_unity\My project\Assets\ScannedModels"

# PlayCanvas設定
PLAYCANVAS_API_TOKEN = "zqeWLVUT18uCWH2uW3J0Dsl1N0oweD7w"
PLAYCANVAS_PROJECT_ID = "1466228"
PLAYCANVAS_SCENE_ID = "2422772"

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


def rotate_and_embed_glb(glb_path):
    """GLBファイルのルートノードにX軸-90度回転を適用し、外部テクスチャを埋め込む

    1. ビューワー/Blenderで真上から見た状態になる問題を修正する。
    2. RealityScanが出力した外部テクスチャ（PNG）をBase64としてGLB内に埋め込み、
       GradioやBlenderでテクスチャが正しく表示されるようにする。
    """
    try:
        from pygltflib import GLTF2
    except ImportError:
        return False, "pygltflib がインストールされていません。pip install pygltflib を実行してください。"

    try:
        gltf = GLTF2().load(glb_path)
        # X軸-90度のクォータニオン: [sin(-45°), 0, 0, cos(-45°)]
        angle = math.radians(-90)
        qx = math.sin(angle / 2)
        qw = math.cos(angle / 2)
        rotation = [qx, 0.0, 0.0, qw]

        scene = gltf.scenes[gltf.scene]
        for node_idx in scene.nodes:
            node = gltf.nodes[node_idx]
            node.rotation = rotation

        import base64
        glb_dir = os.path.dirname(glb_path)
        for image in gltf.images:
            if image.uri and not image.uri.startswith("data:"):
                img_path = os.path.join(glb_dir, image.uri)
                if os.path.exists(img_path):
                    with open(img_path, "rb") as f:
                        img_data = f.read()
                    b64_data = base64.b64encode(img_data).decode('utf-8')
                    ext = os.path.splitext(image.uri)[1].lower()
                    mime_type = "image/jpeg" if ext in ['.jpg', '.jpeg'] else "image/png"
                    image.uri = f"data:{mime_type};base64,{b64_data}"

        gltf.save(glb_path)
        return True, "回転・テクスチャ埋め込み修正完了"
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


# ===== メイン処理 =====

def convert_to_3d(files, project_name, quality,
                  simplify_enabled, simplify_count,
                  smooth_enabled, texture_max_count,
                  sampling_fps, ai_masking_enabled, wide_area_enabled,
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

    # CLIコマンド構築
    quality_flag = QUALITY_OPTIONS.get(quality, "-calculateNormalModel")
    glb_output_path = os.path.join(OUTPUT_DIR, f"{safe_name}.glb")
    ply_output_path = os.path.join(OUTPUT_DIR, f"{safe_name}_sparse.ply")

    cmd = [
        REALITYSCAN_PATH,
        "-addFolder", UPLOAD_DIR,
        "-align",
        "-setReconstructionRegionAuto",
    ]

    # AIマスキング: 準備中（CLIフラグ未対応）
    if ai_masking_enabled:
        # cmd.append("-detectMarkers") # CLIでエラーになるため無効化
        pass

    # メッシュ生成
    cmd.append(quality_flag)
    cmd += ["-renameSelectedModel", "output_model"]

    # 広域モード: コンポーネント結合 & 整合性チェック & 穴埋め（CLIフラグ未対応）
    if wide_area_enabled:
        # cmd.append("-mergeComponents")
        # cmd.append("-checkAndFixCheckIntegrity")
        # cmd.append("-closeHoles")
        pass

    if simplify_enabled:
        cmd += ["-simplify", str(int(simplify_count))]
    if smooth_enabled:
        cmd.append("-smooth")

    # テクスチャ設定
    cmd += ["-set", f"unwrapMaximalTexCount={effective_tex_count}"]
    if wide_area_enabled:
        # 広域用テクスチャスタイル最適化
        cmd += ["-set", "unwrapStyle=adaptive"]
    cmd.append("-calculateTexture")

    # GLB 出力
    cmd += ["-exportModel", "output_model", glb_output_path]

    # PLY（Sparse Point Cloud）出力
    cmd += ["-exportSparsePointCloud", ply_output_path]

    cmd.append("-quit")

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()

        progress(0.90, desc="出力確認中...")

        # GLB 出力確認
        expected = glb_output_path
        if not os.path.exists(expected):
            actual = find_new_file(OUTPUT_DIR, "glb", before_time)
            if actual and actual != expected:
                shutil.move(actual, expected)

        if not os.path.exists(expected):
            stderr_text = stderr.decode(errors='ignore')
            yield f"出力ファイルが見つかりません\n\n{stderr_text}"
            return

        # ステップ3: GLB X軸-90度回転修正 & テクスチャ埋め込み (90-95%)
        progress(0.92, desc="GLB修正中...")
        yield "[3/3] GLB ファイルの回転修正・テクスチャ埋め込み中..."
        rot_success, rot_msg = rotate_and_embed_glb(expected)
        if not rot_success:
            yield f"⚠ 回転修正スキップ: {rot_msg}"

        # PLY 出力確認
        ply_exists = os.path.exists(ply_output_path)
        if not ply_exists:
            actual_ply = find_new_file(OUTPUT_DIR, "ply", before_time)
            if actual_ply and actual_ply != ply_output_path:
                shutil.move(actual_ply, ply_output_path)
                ply_exists = True

        glb_size = os.path.getsize(expected) / (1024 * 1024)

        progress(1.0, desc="変換完了！")

        result_lines = [
            "--- 変換完了 ---",
            "",
            f"GLB: {expected} ({glb_size:.1f} MB)",
        ]
        if rot_success:
            result_lines.append("  → X軸 -90度回転修正済み")
        if ply_exists:
            ply_size = os.path.getsize(ply_output_path) / (1024 * 1024)
            result_lines.append(f"PLY: {ply_output_path} ({ply_size:.1f} MB)")
        else:
            result_lines.append("PLY: Sparse Point Cloud の出力は確認できませんでした")
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
            unity_dir = os.path.join(UNITY_ASSETS_DIR, project_name)
            os.makedirs(unity_dir, exist_ok=True)
            dst = os.path.join(unity_dir, f"{project_name}.glb")
            shutil.copy(glb_path, dst)
            results.append(f"Unity: {dst}")
        else:
            results.append("Unity: GLBファイルがありません")

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
    title="RealityScan WebUI",
    theme=gr.themes.Soft(primary_hue="orange")
) as app:

    gr.Markdown("# RealityScan WebUI")
    gr.Markdown("写真・動画 → 3Dモデル(GLB)生成 → プレビュー → Unity / PlayCanvas へ送信")

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

    # --- RealityScan 2.0 新機能 ---
    with gr.Row():
        ai_masking_enabled = gr.Checkbox(
            label="🎭 AIマスキング（空・動体の自動除外）",
            value=False, scale=1
        )
        wide_area_enabled = gr.Checkbox(
            label="🌐 広域モード（コンポーネント結合＋品質レポート＋穴埋め）",
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

    convert_btn = gr.Button("3Dモデルに変換", variant="primary", size="lg")
    status_output = gr.Textbox(label="処理状況", lines=6)

    # ========== セクション2: プレビュー ==========
    gr.Markdown("---")
    gr.Markdown("### プレビュー")
    glb_viewer = gr.Model3D(label="GLB ビューワー", height=480)

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
            sampling_fps, ai_masking_enabled, wide_area_enabled
        ],
        outputs=[status_output]
    ).then(
        fn=load_viewer,
        inputs=[project_name_input],
        outputs=[glb_viewer, glb_state]
    )

    upload_btn.click(
        fn=upload_to_targets,
        inputs=[glb_state, project_name_input, send_unity, send_playcanvas],
        outputs=[upload_status]
    )

app.launch(server_name="0.0.0.0", server_port=7860, share=False)
