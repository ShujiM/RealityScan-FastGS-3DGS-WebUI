# CLAUDE.md — RealityScan / FastGS WebUI プロジェクト仕様書

> このファイルは **Claude Code** および **Antigravity** 等の AI コーディングツールが
> プロジェクトの文脈を理解するための共有仕様書です。

## プロジェクト概要

iPhone 写真/動画 → RealityScan 2.1 CLI → GLB メッシュ + 3DGS (FastGS via Docker) → プレビュー → Unity / PlayCanvas アップロード

**Gradio ベースの WebUI** で全工程を自動化するツール。

## ファイル構成

```
D:\RealityScanWebUI\
├── main.py                 # Gradio UI（エントリーポイント: python main.py）
├── config.py               # パス設定・品質オプション・定数（.env から秘密情報読込）
├── .env                    # 秘密情報（PlayCanvas APIトークン等）※ gitignore 済み
├── requirements.txt        # pip 依存パッケージ
├── modules/
│   ├── __init__.py
│   ├── utils.py            # 汎用関数（フレーム抽出、GLB回転修正、進捗パーサー）
│   ├── processor.py        # RealityScan CLI実行・進捗モニタリング・停止制御
│   ├── gs_handler.py       # FastGS Docker学習・ログ解析・ステータス表示
│   └── uploader.py         # Unity / PlayCanvas アップロード
├── app.py                  # 旧モノリシック版（後方互換用に残存、main.py への移行済み）
├── Dockerfile.speedysplat  # FastGS Docker イメージ (CUDA 11.6 + PyTorch 1.12)
├── docker-compose.yml      # fastgs サービス定義 (GPU)
├── scripts/
│   └── run_speedysplat.sh  # Docker 内 FastGS 学習スクリプト
├── documentation_settings.md  # 設定項目の初心者向け日本語ガイド
├── uploads/                # 一時入力画像（gitignore）
├── output/                 # 出力 GLB/PLY/COLMAP（gitignore）
└── logs/                   # ログ・進捗・クラッシュ報告（gitignore）
```

## 起動方法

```bash
python main.py              # Gradio サーバー起動 → http://localhost:7860
python app.py               # 旧版（非推奨だが動作する）
```

## 重要な技術仕様

### RealityScan 2.1 CLI コマンド順序

CLI コマンドは **実行順序が厳密** に重要。現在の正しい順序:

```
-headless → -silent → -stdConsole → -writeProgress
→ -addFolder (画像)
→ -generateAIMasks (オプション)
→ -align → -setReconstructionRegionAuto
→ -mergeComponents (広域モードのみ、アライメント操作)
→ -selectMaximalComponent
→ -calculateNormalModel / -calculatePreviewModel / -calculateHighModel
→ -closeHoles (広域モードのみ、メッシュ操作)
→ -simplify → -smooth (オプション)
→ -renameSelectedModel "output_model"
→ -set unwrapMaximalTexCount=N → -calculateTexture
→ -exportModel → -exportSparsePointCloud
→ -quit
```

**注意**: `-mergeComponents` はアライメント操作（メッシュ生成前）、`-closeHoles` はメッシュ操作（メッシュ生成後）に分離すること。`-renameSelectedModel` は全メッシュ操作の後に配置すること。

### COLMAP エクスポートについて

RealityScan 2.1 の `-exportRegistration`、`-exportUndistortedImages`、`-exportMapsAndMask` は長時間ハングする場合があるため、現在は CLI から除去済み。FastGS の COLMAP スキップモードは、出力フォルダに既存の COLMAP データがあるかファイルシステムで検出する方式に変更。

### GLB 後処理 (modules/utils.py: rotate_and_pack_glb)

- **X軸 -90° 回転**: フォトグラメトリ出力の Z-up → GLTF Y-up 変換（Blender で正立表示）
- **テクスチャ埋め込み**: 外部 PNG/JPG を GLB バイナリバッファに統合（4-byte alignment）
- **クォータニオン合成**: 既存ノード回転と Hamilton 積で合成（上書きではない）

### 進捗表示 (modules/processor.py)

1. **-writeProgress ファイル**: RealityScan の XML 進捗出力を 2 秒間隔でパース
2. **stdout フェーズ検出**: `"Executing command"` を解析して日本語フェーズ名を表示
3. **テキストベース表示**: Gradio の `progress()` オーバーレイは使わない（ログを隠すため）
4. **毎回 yield**: 条件分岐なしで 2 秒ごとに必ず yield してテキストを更新

### FastGS / 3DGS (modules/gs_handler.py)

- Docker (nvidia/cuda:11.6.2) で FastGS train.py を 30,000 iteration 実行
- COLMAP スキップモード: `{name}_colmap/` に既存データがあれば COLMAP 推定をスキップ
- ログ解析: `{name}_fastgs.log` からステップ・イテレーション・エラーを検出

## 既知の制約 / 注意事項

- **Gradio 6.0**: `show_copy_button` 等一部パラメータが `Blocks` から `launch()` に移動。`theme` も `launch()` に渡す必要があるが現在は警告のみ（動作には影響なし）
- **pygltflib**: GLB テクスチャ埋め込みに必要。未インストールだと回転修正もスキップされる
- **psutil**: 停止ボタンで RealityScan プロセスを検出するために使用（なくても taskkill で代替）
- **RealityScan パス**: `config.py` の `_find_realityscan()` が 2.1 → 2.0 の順に自動検出
- **app.py と modules/ の二重管理**: app.py（旧）と modules/（新）に同等のロジックが存在する。新機能は modules/ 側に実装すること。Antigravity が app.py を直接編集した場合は modules/ にも反映が必要

## 協業ルール（Claude Code ↔ Antigravity）

1. **作業後は必ず git commit** してからツールを切り替える
2. **このファイル (CLAUDE.md)** を仕様変更時に更新する
3. **modules/ が正式版**: app.py への変更は modules/ にも同期すること
4. **コミットメッセージ**: Co-Authored-By で誰が変更したか明示する
