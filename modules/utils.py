"""
modules/utils.py — 汎用ヘルパー関数

フレーム抽出、ファイル名安全化、GLB修正、進捗パーサーなど
プロジェクト横断で使い回せるユーティリティを集約。
"""

import os
import re
import math
import glob
import subprocess

from config import FFMPEG_PATH


# ──────────────────────────────────────────────
# ファイル / ファイル名ユーティリティ
# ──────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """ファイル名に安全な文字列へ変換"""
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '', name)
    return safe if len(safe) >= 2 else "model_" + safe


def find_new_file(output_dir: str, extension: str, before_time: float):
    """指定時刻以降に作成されたファイルのうち最新を返す"""
    found = glob.glob(os.path.join(output_dir, f"*.{extension}"))
    new_files = [f for f in found if os.path.getmtime(f) > before_time]
    return max(new_files, key=os.path.getmtime) if new_files else None


# ──────────────────────────────────────────────
# 動画フレーム抽出
# ──────────────────────────────────────────────

def extract_frames(video_path: str, output_dir: str, fps: float = 2.0) -> int:
    """動画からフレームを抽出（可変FPS対応）

    Returns:
        抽出されたフレーム数
    """
    cmd = [
        FFMPEG_PATH, "-i", video_path,
        "-vf", f"fps={fps}", "-q:v", "2",
        os.path.join(output_dir, "frame_%04d.jpg"), "-y"
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return len([f for f in os.listdir(output_dir) if f.endswith('.jpg')])


# ──────────────────────────────────────────────
# VRAM 最適化
# ──────────────────────────────────────────────

def auto_adjust_texture_count(image_count: int, vram_gb: int = 12) -> int:
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


# ──────────────────────────────────────────────
# GLB 後処理（回転修正 + テクスチャ埋め込み）
# ──────────────────────────────────────────────

def rotate_and_pack_glb(glb_path: str):
    """GLBファイルのX軸-90度回転＋外部テクスチャをバイナリバッファに統合

    1. ビューワー/Blenderで真上から見た状態になる問題を修正する。
    2. 外部テクスチャPNGがあればGLBバイナリバッファに正しく埋め込み、
       Gradio等の単一ファイルビューワーでもテクスチャが表示されるようにする。

    Returns:
        (success: bool, message: str)
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

                    blob = gltf.binary_blob()
                    if blob is None:
                        blob = b""
                    offset = len(blob)
                    blob += img_data

                    if len(gltf.buffers) == 0:
                        from pygltflib import Buffer
                        gltf.buffers.append(Buffer(byteLength=len(blob)))
                    else:
                        gltf.buffers[0].byteLength = len(blob)

                    bv_index = len(gltf.bufferViews)
                    gltf.bufferViews.append(BufferView(
                        buffer=0,
                        byteOffset=offset,
                        byteLength=len(img_data),
                    ))

                    ext = os.path.splitext(image.uri)[1].lower()
                    image.mimeType = "image/jpeg" if ext in ['.jpg', '.jpeg'] else "image/png"
                    image.bufferView = bv_index
                    image.uri = None

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


# ──────────────────────────────────────────────
# プログレスバー / 進捗パーサー
# ──────────────────────────────────────────────

def format_progress_bar(pct: int, width: int = 30) -> str:
    """テキストベースのプログレスバーを生成"""
    if pct < 0:
        return f"{'━' * width}  ❌ ERROR"
    filled = int(width * pct / 100)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"{bar}  {pct}%"


def parse_realityscan_progress(progress_file: str):
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
