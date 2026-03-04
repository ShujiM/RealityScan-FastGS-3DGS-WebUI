"""
main.py — Gradio WebUI エントリーポイント

UI 定義に集中し、処理ロジックは各モジュールからインポートする。
"""

import os
import gradio as gr

from config import (
    REALITYSCAN_PATH, QUALITY_OPTIONS,
    REST_API_ENABLED, REST_API_URL,
    SERVER_HOST, SERVER_PORT,
)
from modules.processor import convert_to_3d, load_viewer, stop_processing
from modules.gs_handler import check_fastgs_status
from modules.uploader import upload_to_targets


# ──────────────────────────────────────────────
# WebUI 定義
# ──────────────────────────────────────────────

with gr.Blocks(
    title="RealityScan/FastGS WebUI",
    theme=gr.themes.Soft(primary_hue="orange")
) as app:

    gr.Markdown("# RealityScan 2.1 / FastGS WebUI")
    gr.Markdown("写真・動画 → 3Dモデル(GLB)生成 → プレビュー → Unity / PlayCanvas へ送信")
    gr.Markdown(
        f"*RealityScan: `{os.path.basename(os.path.dirname(REALITYSCAN_PATH))}` | "
        f"ヘッドレスモード | REST/gRPC: {'✅ 有効' if REST_API_ENABLED else '⬜ 無効'}*"
    )

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

    with gr.Row():
        convert_btn = gr.Button("3Dモデル・3DGS変換を開始", variant="primary", size="lg", scale=4)
        stop_btn = gr.Button("⏹ 停止", variant="stop", size="lg", scale=1)
    status_output = gr.Textbox(label="処理状況（リアルタイム進捗表示）", lines=15)

    # ========== セクション2: プレビュー ==========
    gr.Markdown("---")

    with gr.Tabs():
        with gr.Tab("GLB (ポリゴンメッシュ)"):
            gr.Markdown("RealityScan で生成されたテクスチャ付きポリゴンメッシュです。")
            glb_viewer = gr.Model3D(label="GLB ビューワー", height=480)

        with gr.Tab("3DGS / PLY ビューワー (FastGS)"):
            gr.Markdown(
                "Docker で学習を実行中の 3DGS モデル（Splat形式PLY）の"
                "状態確認とプレビューを行います。"
            )

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
            gr.Markdown(
                "✅ 学習が完了し PLY ファイルが出力されたら、"
                "上のファイルを手元にダウンロードし、下のビューワーに "
                "**ドラッグ＆ドロップ** して閲覧してください。"
            )
            gr.HTML(
                '<iframe src="https://superspl.at/editor" '
                'width="100%" height="600px" '
                'style="border: 1px solid #ccc; border-radius: 8px;"></iframe>'
            )

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

    stop_btn.click(
        fn=stop_processing,
        inputs=[],
        outputs=[status_output]
    )

    upload_btn.click(
        fn=upload_to_targets,
        inputs=[glb_state, project_name_input, send_unity, send_playcanvas],
        outputs=[upload_status]
    )


# ──────────────────────────────────────────────
# サーバー起動
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app.launch(server_name=SERVER_HOST, server_port=SERVER_PORT, share=False)
