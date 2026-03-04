"""
modules/uploader.py — 外部送信（Unity / PlayCanvas）

GLB ファイルを各プラットフォームへアップロードする機能を集約。
"""

import os
import json
import shutil
import requests

from config import (
    PLAYCANVAS_API_TOKEN, PLAYCANVAS_PROJECT_ID, PLAYCANVAS_SCENE_ID,
    UNITY_ASSETS_DIR,
)


# ──────────────────────────────────────────────
# PlayCanvas アップロード
# ──────────────────────────────────────────────

def upload_to_playcanvas(glb_path: str, model_name: str):
    """PlayCanvas にGLBをアップロードしてシーンに配置

    Returns:
        (success: bool, result: str)
    """
    headers = {"Authorization": f"Bearer {PLAYCANVAS_API_TOKEN}"}

    with open(glb_path, 'rb') as f:
        files = {'file': (f"{model_name}.glb", f, 'model/gltf-binary')}
        data = {
            'name': model_name,
            'projectId': PLAYCANVAS_PROJECT_ID,
            'preload': 'true',
        }
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
        "position": [0, 0, 0],
        "rotation": [0, 0, 0],
        "scale": [1, 1, 1],
    }
    response2 = requests.post(
        f"https://playcanvas.com/api/scenes/{PLAYCANVAS_SCENE_ID}/entities",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps(entity_data)
    )
    if response2.status_code in [200, 201]:
        return True, asset_id
    return True, f"アセットID:{asset_id}（シーン配置は手動で確認）"


# ──────────────────────────────────────────────
# 統合送信関数
# ──────────────────────────────────────────────

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
                    shutil.copy2(
                        os.path.join(output_dir, f),
                        os.path.join(project_dir, f)
                    )
            results.append(
                f"Unity: {os.path.join(project_dir, os.path.basename(glb_path))}"
                f" および関連テクスチャをコピーしました"
            )
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
