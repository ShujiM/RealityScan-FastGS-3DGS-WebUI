"""
config.py — アプリケーション設定・定数

パス設定、品質オプションなど秘密情報以外の定数を集約。
APIトークン等の秘密情報は .env から読み込む。
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# パス設定
# ──────────────────────────────────────────────

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
FFMPEG_PATH      = r"D:\ffmpeg\bin\ffmpeg.exe"

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR   = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")
LOG_DIR      = os.path.join(BASE_DIR, "logs")
CRASH_LOG_DIR = os.path.join(LOG_DIR, "crash")
PROGRESS_DIR  = os.path.join(LOG_DIR, "progress")

UNITY_ASSETS_DIR = r"D:\RealityScan_unity\My project\Assets\ScannedModels"

# 自動作成
for _d in [UPLOAD_DIR, OUTPUT_DIR, LOG_DIR, CRASH_LOG_DIR, PROGRESS_DIR]:
    os.makedirs(_d, exist_ok=True)

# ──────────────────────────────────────────────
# PlayCanvas 設定（秘密情報は .env から）
# ──────────────────────────────────────────────

PLAYCANVAS_API_TOKEN  = os.getenv("PLAYCANVAS_API_TOKEN", "")
PLAYCANVAS_PROJECT_ID = os.getenv("PLAYCANVAS_PROJECT_ID", "")
PLAYCANVAS_SCENE_ID   = os.getenv("PLAYCANVAS_SCENE_ID", "")

# ──────────────────────────────────────────────
# REST/gRPC API 設定（秘密情報は .env から）
# ──────────────────────────────────────────────

REST_API_ENABLED = os.getenv("REST_API_ENABLED", "false").lower() == "true"
REST_API_URL     = os.getenv("REST_API_URL", "http://localhost:20180")

# ──────────────────────────────────────────────
# 品質オプション（UIドロップダウン用）
# ──────────────────────────────────────────────

QUALITY_OPTIONS = {
    "プレビュー（最速）": "-calculatePreviewModel",
    "ノーマル（バランス）": "-calculateNormalModel",
    "高品質（低速）": "-calculateHighModel",
}

# ──────────────────────────────────────────────
# RealityScan ステップ名の日本語マッピング
# ──────────────────────────────────────────────

RS_STEP_NAMES = {
    "Feature detection":       "特徴点検出",
    "Matching":                "マッチング",
    "Alignment":               "アライメント",
    "Depth map computation":   "深度マップ計算",
    "Normal model":            "メッシュ生成",
    "Preview model":           "プレビューメッシュ生成",
    "High model":              "高品質メッシュ生成",
    "Model computation":       "メッシュ計算",
    "Unwrap":                  "UV展開",
    "Texturing":               "テクスチャ計算",
    "Coloring":                "カラーリング",
    "Simplification":          "メッシュ簡略化",
    "Smoothing":               "スムージング",
    "Export":                  "エクスポート",
    "AI Masking":              "AIマスキング",
}

# ──────────────────────────────────────────────
# Gradio サーバー設定
# ──────────────────────────────────────────────

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 7860
