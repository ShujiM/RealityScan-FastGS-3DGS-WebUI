# RealityScan WebUI & FastGS 3DGS

RealityScan CLI を用いた 3D ポリゴンメッシュ (GLB) の自動生成と、**FastGS** (CVPR 2025: "Training 3D Gaussian Splatting in 100 Seconds") による高速 3D Gaussian Splatting モデル (.ply) 学習を行うための統合 Web ツール (Gradio) です。

## 主な機能

1. **RealityScan 2.0 操作パネル**
   - 動画(MP4)や画像(JPG/PNG)をアップロードし、可変FPSでフレームを抽出。
   - VRAM最適化を自動で行い、高品質な 3D ポリゴンメッシュ (GLB) を生成。
   - pygltflib による X軸回転修正 + テクスチャ埋め込み。
   - Unity / PlayCanvas への自動アップロード転送機能。

2. **FastGS 3DGS 自動学習 (Docker連携)**
   - RealityScan の処理と並行して、Docker コンテナ内で COLMAP + **FastGS** による高速 3DGS 学習を実行。
   - CUDA拡張モジュール (diff-gaussian-rasterization, simple-knn, fused-ssim) を含むビルド済み環境。
   - プログレスバー + ステップ表示 + 自動更新タイマーでリアルタイムに学習状況を監視。

3. **SuperSplat ビューワー統合**
   - 出力された 3DGS モデル (`.ply`) を、WebUI 上に埋め込まれた SuperSplat エディタにドラッグ＆ドロップして即座にプレビュー・編集可能。

---

## 事前準備（前提条件）

- **OS**: Windows 10/11
- **GPU**: NVIDIA GPU (推奨: VRAM 12GB以上)
- **ソフトウェア**:
  1. [Python 3.10+](https://www.python.org/downloads/)
  2. [Docker Desktop](https://www.docker.com/products/docker-desktop/) (WSL2バックエンド有効化済、NVIDIA Container Toolkit 導入済)
  3. [RealityScan 2.0 CLI](https://www.capturingreality.com/realityscan-cli)
  4. FFmpeg (動画からフレーム抽出に必要。パスを通すか `app.py` 冒頭の `FFMPEG_PATH` を設定)

---

## 初回セットアップ

1. **Python パッケージのインストール**

   ```bash
   cd D:\RealityScanWebUI
   pip install gradio requests pygltflib
   ```

2. **FastGS Docker イメージのビルド**
   COLMAP + FastGS (CUDA拡張含む) の学習専用コンテナ環境をビルドします。初回は CUDA 拡張のコンパイルに10〜20分程度かかります。

   ```bash
   docker-compose build
   ```

---

## 起動方法

```bash
cd D:\RealityScanWebUI
python app.py
```

ターミナルに `Running on local URL:  http://127.0.0.1:7860` と表示されたら、ブラウザでその URL を開いてください。

---

## 操作マニュアル

### 1. データの入力と設定

- **ファイルアップロード**: スキャンしたい動画 (.mp4 等) または画像セット (.jpg, .png の複数選択可) をドロップします。動画の場合は「動画抽出FPS」スライダーで1秒間あたりの切り出し枚数を調整できます。
- **モデル名**: `garden01` のような半角英数でプロジェクト名を入力します。出力ファイル名に利用されます。
- **メッシュ品質**: プレビュー（最速）/ ノーマル（バランス）/ 高品質（低速）から選択。

### 2. RealityScan 設定と FastGS 3DGS 学習

- **FastGS (3DGS) 同時学習**: チェックを入れると、GLB 生成後に Docker コンテナが起動し、COLMAP によるカメラポーズ推定 → FastGS による 3DGS 学習 (.ply 出力) を自動実行します。
- **AIマスキング / 広域モード**: 不要領域の除外や複数スキャン結合のためのUI設定です（CLI対応状況により一部準備中）。
- **詳細設定**: メッシュ簡略化、スムージング、テクスチャ最大枚数、動画抽出FPSの微調整が可能です。

### 3. 処理開始

- 「**3Dモデル・3DGS変換を開始**」ボタンを押します。
- 処理状況テキストに進行状況が表示されます。
- GLB 生成完了後、自動的にGLBビューワーに 3D モデルが表示されます。

### 4. FastGS 3DGS 学習ステータスの確認

- 「**3DGS / PLY ビューワー (FastGS)**」タブを開きます。
- 「**自動更新 (5秒間隔)**」にチェックを入れると、学習の進捗がリアルタイムで更新されます。
- プログレスバーとステップ表示で現在の処理段階を確認できます:
  - [1/4] 画像コピー
  - [2/4] COLMAP カメラポーズ推定 (SfM) — CPU処理のため大量画像では時間がかかります
  - [3/4] FastGS 高速学習 — GPU で高速実行 (iteration 進捗表示)
  - [4/4] 出力ファイル確認
- `✅ 学習完了！` と表示されれば完了です。
- 「**学習済 Splat PLY**」ファイルをダウンロードし、下の **SuperSplat ビューワーにドラッグ＆ドロップ** すると 3DGS モデルをプレビューできます。

### 5. Unity / PlayCanvas への転送

- 画面下部の「送信先」セクションから、Unity の Assets フォルダや PlayCanvas プロジェクトへ GLB データを自動アップロードできます。

---

## トラブルシューティング

- **Q. Docker ビルドで `Unknown CUDA arch` エラーが出る**
  - `Dockerfile.speedysplat` の `TORCH_CUDA_ARCH_LIST` を確認してください。PyTorch 1.12.1 は arch 8.9/9.0 を認識しないため、`8.6+PTX` で前方互換性を確保しています。
- **Q. FastGS 学習で `Could not recognize scene type!` エラー**
  - COLMAP が正常に `sparse/` ディレクトリを生成できていません。`run_speedysplat.sh` で `--no_gpu` フラグが指定されているか確認してください（apt版 COLMAP は CUDA 未サポート）。
- **Q. Docker コンテナが起動しない / 学習が始まらない**
  - Docker Desktop が起動していることを確認してください。
  - `docker-compose run --rm fastgs bash` でコンテナに入り、手動で状況確認できます。
- **Q. RealityScan CLI コマンドが見つからない**
  - `app.py` 冒頭の `REALITYSCAN_PATH` が正しいパスを指しているか確認してください。
- **Q. GLB が真上を向いている**
  - pygltflib による X軸 -90度回転修正が自動で適用されます。修正が効かない場合は pygltflib がインストールされているか確認してください。
