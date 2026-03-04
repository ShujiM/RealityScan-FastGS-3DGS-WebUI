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

def _quat_multiply(q1: list, q2: list) -> list:
    """クォータニオンの合成 (Hamilton product)  形式: [x, y, z, w]

    q1 × q2 の順で適用される（q1 が後から掛かる「追加回転」）。
    """
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return [
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
    ]


def rotate_and_pack_glb(glb_path: str):
    """GLBファイルのX軸-90度回転＋外部テクスチャをバイナリバッファに統合

    1. Blender / ビューワーで「真上から見た状態」になる問題を修正する。
       X軸 -90° 回転を既存ノード回転と合成してシーンのルートノードに適用。
    2. 外部テクスチャ PNG が同ディレクトリにあればバイナリバッファに埋め込む。
       GLTF spec に準拠した 4-byte アライメントで追加する。

    Returns:
        (success: bool, message: str)
    """
    import traceback

    try:
        from pygltflib import GLTF2, BufferView, Buffer
    except ImportError:
        return False, (
            "pygltflib がインストールされていません。\n"
            "  pip install pygltflib  を実行してください。"
        )

    try:
        gltf = GLTF2().load(glb_path)

        # ── 1. X軸 -90° 回転クォータニオン ────────────────────────
        # モデルが「真上から見た状態（寝た姿勢）」→ 「正立」に補正。
        # -90°X: sin(-45°)=-0.7071, cos(-45°)=0.7071
        angle = math.radians(-90)
        new_rot = [math.sin(angle / 2), 0.0, 0.0, math.cos(angle / 2)]

        scene = gltf.scenes[gltf.scene]
        if not scene.nodes:
            return False, "GLB にシーンノードが見つかりません"

        for node_idx in scene.nodes:
            node = gltf.nodes[node_idx]
            # 既存の回転と合成（上書きではなく乗算）
            existing = list(node.rotation) if node.rotation else [0.0, 0.0, 0.0, 1.0]
            node.rotation = _quat_multiply(new_rot, existing)

        # ── 2. 外部テクスチャの埋め込み ────────────────────────────
        glb_dir = os.path.dirname(glb_path)
        embedded_count = 0
        embed_errors = []

        # 全画像を走査してバイナリブロブに追加（ループ終了後に一括 set_binary_blob）
        blob = gltf.binary_blob() or b""

        for image in (gltf.images or []):
            if not (image.uri and not image.uri.startswith("data:")):
                continue

            # ファイルを検索（大文字小文字を区別しないフォールバック付き）
            img_path = os.path.join(glb_dir, image.uri)
            if not os.path.exists(img_path):
                # 大文字小文字が違う場合のフォールバック
                uri_lower = os.path.basename(image.uri).lower()
                for fname in os.listdir(glb_dir):
                    if fname.lower() == uri_lower:
                        img_path = os.path.join(glb_dir, fname)
                        break

            if not os.path.exists(img_path):
                embed_errors.append(f"テクスチャ未発見: {image.uri}")
                continue

            with open(img_path, "rb") as f:
                img_data = f.read()

            # GLTF spec: bufferView.byteOffset は 4 バイトアライメント必須
            pad_len = (4 - len(blob) % 4) % 4
            blob += b"\x00" * pad_len
            offset = len(blob)
            blob += img_data

            # BufferView を追加
            bv_index = len(gltf.bufferViews)
            gltf.bufferViews.append(BufferView(
                buffer=0,
                byteOffset=offset,
                byteLength=len(img_data),
            ))

            # image を bufferView 参照に切り替え
            ext = os.path.splitext(img_path)[1].lower()
            image.mimeType = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            image.bufferView = bv_index
            image.uri = None
            embedded_count += 1

        # バッファサイズ更新 & blob 書き戻し（ループ外で一括）
        if embedded_count > 0:
            if not gltf.buffers:
                gltf.buffers.append(Buffer(byteLength=len(blob)))
            else:
                gltf.buffers[0].byteLength = len(blob)
            gltf.set_binary_blob(blob)

        # ── 3. 保存 ─────────────────────────────────────────────────
        gltf.save(glb_path)

        # 結果メッセージ
        parts = ["X軸 -90° 回転修正済み"]
        if embedded_count > 0:
            parts.append(f"テクスチャ {embedded_count} 枚埋め込み完了")
        if embed_errors:
            parts.append("⚠ " + " / ".join(embed_errors))
        return True, " ＋ ".join(parts)

    except Exception:
        return False, f"GLB修正エラー:\n{traceback.format_exc()}"


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
