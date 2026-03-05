# RealityScan 2.1 / FastGS 3DGS WebUI

RealityScan 2.1 CLI による 3D ポリゴンメッシュ (GLB) 自動生成と、**FastGS** (CVPR 2025: "Training 3D Gaussian Splatting in 100 Seconds") による高速 3D Gaussian Splatting モデル (.ply) 学習を行う統合 WebUI (Gradio) です。

## 主な機能

### RealityScan 2.1 ポリゴンメッシュ生成

- 動画 (MP4/MOV) や画像 (JPG/PNG) のアップロード、可変 FPS フレーム抽出
- VRAM 最適化による高品質 3D ポリゴンメッシュ (GLB) 生成
- pygltflib による X 軸 -90 度回転修正 + テクスチャ埋め込み
- AI マスキング（空・動体の自動除外）
- 広域モード（コンポーネント結合 + 穴埋め）
- メッシュ簡略化・スムージング対応
- リアルタイム進捗表示（フェーズ検出 + プログレスバー）
- 変換完了時の総時間表示

### FastGS 3DGS 自動学習 (Docker 連携)

- RealityScan 処理完了後、Docker コンテナ内で自動的に 3DGS 学習を実行
- COLMAP による SfM: sequential_matcher + OPENCV 単一カメラモデルで動画フレームに最適化
- 学習完了後、PLY を X 軸 -90 度回転して GLB と同じ座標系で出力
- プログレスバー + ステップ表示 + 自動更新タイマー (デフォルト ON / 5 秒間隔)

### SuperSplat ビューワー統合

- 3DGS モデル (.ply) を WebUI 上の SuperSplat エディタにドラッグ＆ドロップして即座にプレビュー

### 送信先連携

- Unity Assets フォルダへの GLB 自動コピー
- PlayCanvas API 経由のアップロード

---

## 必要環境

| 項目 | 要件 |
|------|------|
| OS | Windows 10/11 |
| GPU | NVIDIA GPU (VRAM 12 GB 以上推奨) |
| Python | 3.10+ |
| Docker | Docker Desktop (WSL2 + NVIDIA Container Toolkit) |
| RealityScan | [RealityScan 2.1 CLI](https://www.capturingreality.com/realityscan-cli) |
| FFmpeg | パスを通すか `config.py` の `FFMPEG_PATH` を設定 |

---

## プロジェクト構成

```
RealityScanWebUI/
├── main.py                  # Gradio WebUI エントリーポイント（UI定義）
├── config.py                # 設定・定数（パス、品質オプション等）
├── modules/
│   ├── processor.py         # RealityScan CLI 実行・進捗管理
│   ├── gs_handler.py        # FastGS 学習制御・ログ解析・ステータス表示
│   ├── uploader.py          # Unity / PlayCanvas 送信
│   └── utils.py             # ユーティリティ（GLB回転、フレーム抽出等）
├── scripts/
│   ├── run_speedysplat.sh   # Docker内 COLMAP + FastGS 実行スクリプト
│   └── rotate_ply.py        # PLY X軸-90度回転
├── docker-compose.yml       # FastGS Docker定義
├── Dockerfile.speedysplat   # COLMAP + FastGS + CUDA拡張ビルド
├── .env                     # APIトークン等（git管理外）
├── uploads/                 # アップロードされた画像・動画
└── output/                  # 生成された GLB / PLY / ログ
```

---

## 初回セットアップ

### 1. Python パッケージ

```bash
cd D:\RealityScanWebUI
pip install -r requirements.txt
```

### 2. 環境設定

`config.py` を確認し、以下のパスが正しいことを確認:

- `REALITYSCAN_PATH` — RealityScan 2.1 CLI の実行ファイル（自動検出）
- `FFMPEG_PATH` — FFmpeg の実行ファイル
- `UNITY_ASSETS_DIR` — Unity Assets フォルダ（Unity 連携時）

PlayCanvas 連携を使用する場合は `.env` にトークンを設定:

```
PLAYCANVAS_API_TOKEN=your_token
PLAYCANVAS_PROJECT_ID=your_project_id
PLAYCANVAS_SCENE_ID=your_scene_id
```

### 3. FastGS Docker イメージのビルド

```bash
docker-compose build
```

> 初回は CUDA 拡張のコンパイルに 10〜20 分かかります。

---

## 起動方法

```bash
cd D:\RealityScanWebUI
python main.py
```

ブラウザで `http://127.0.0.1:7860` を開いてください。

---

## 使い方

### 1. データ入力

- 動画 (.mp4 等) または画像セット (.jpg/.png) をドロップ
- モデル名を入力（半角英数: `garden01` 等）
- メッシュ品質を選択: プレビュー / ノーマル / 高品質

### 2. オプション設定

| 設定 | 説明 |
|------|------|
| FastGS (3DGS) 同時学習 | GLB 生成後に Docker で 3DGS 学習を自動実行 |
| AI マスキング | 空・動体を自動検出して除外（屋外シーン推奨） |
| 広域モード | 複数コンポーネント結合 + 穴埋め（広い範囲のスキャン用） |
| メッシュ簡略化 | ポリゴン数削減（Unity 等の軽量化向け） |
| スムージング | メッシュ表面の平滑化 |
| 動画抽出 FPS | 低い = 少ない画像 / 高速、高い = 多い画像 / 高精細 |

### 3. 変換実行

「3D モデル・3DGS 変換を開始」をクリック。処理状況がリアルタイムで表示されます。

### 4. 3DGS 学習状況

「3DGS / PLY ビューワー (FastGS)」タブで自動更新されます:

- [1/4] 画像コピー
- [2/4] COLMAP カメラポーズ推定 (SfM)
- [3/4] FastGS 高速学習 (iteration 進捗表示)
- [4/4] 出力ファイル確認 + PLY 回転

学習完了後、PLY ファイルを SuperSplat ビューワーにドラッグ＆ドロップしてプレビューできます。

---

## 技術的な仕様

### CLI コマンド実行順序

RealityScan 2.1 CLI のコマンドは以下の順序で実行されます:

```
-generateAIMasks (AIマスキング有効時)
→ -align → -setReconstructionRegionAuto
→ -mergeComponents (広域モード時)
→ -selectMaximalComponent (アライメントコンポーネント選択)
→ メッシュ生成 (Preview/Normal/High)
→ -selectMaximalComponent (メッシュ不要部品の除外)
→ -closeHoles (広域モード時)
→ -simplify → -smooth (オプション)
→ -renameSelectedModel → -calculateTexture
→ -exportModel (GLB) → -exportSparsePointCloud (PLY)
```

### COLMAP 設定

Docker 内で以下の COLMAP コマンドを直接実行:

- `feature_extractor` — OPENCV カメラモデル + single_camera モード
- `sequential_matcher` — 動画フレームの連続性を活かした高精度マッチング
- `mapper` — SfM 再構築
- `image_undistorter` — 歪み補正

### PLY 回転

3DGS 学習完了後、`rotate_ply.py` で X 軸 -90 度回転を適用:

- 位置 (x, y, z)、法線 (nx, ny, nz)、四元数 (rot_0〜rot_3) を回転

---

## トラブルシューティング

**Docker ビルドで `Unknown CUDA arch` エラー**
→ `Dockerfile.speedysplat` の `TORCH_CUDA_ARCH_LIST` を確認。`8.6+PTX` で前方互換性を確保。

**COLMAP で画像がほとんど登録されない**
→ 入力動画のブレ・ピンぼけ、カメラワークの速さが原因の可能性。FPS を 4〜5 に上げて画像間の重なりを増やすか、ゆっくり撮影した動画で再試行。

**GLB に暗いポリゴンが表示される**
→ 屋外シーンで AI マスキングを OFF にすると、空や遠景がメッシュ化されます。AI マスキングを ON にして再実行。

**Docker コンテナが起動しない**
→ Docker Desktop 起動を確認。`docker-compose run --rm fastgs bash` で手動確認可能。

**GLB が真上を向いている**
→ pygltflib による X 軸 -90 度回転修正は自動適用されます。`pip install pygltflib` を確認。

---

## ライセンス

- [RealityScan](https://www.capturingreality.com/) — 商用ライセンス
- [FastGS / 3DGS](https://github.com/graphdeco-inria/gaussian-splatting) — 研究用ライセンス
- [SuperSplat](https://superspl.at/) — MIT License
