# RealityScan WebUI & SpeedySplat 3DGS

RealityScan CLI を用いた 3D ポリゴンメッシュ (GLB) の自動生成と、最新の 3D Gaussian Splatting (SpeedySplat) による高速・高品質な SPlat モデル (.ply) 学習を行うための統合 Web ツール (Gradio) です。

## 🌟 主な機能

1. **RealityScan 2.0 操作パネル**
   - 動画(MP4)や画像(JPG/PNG)をアップロードし、最適なFPSでフレームを抽出。
   - VRAM最適化を自動で行い、高品質な 3D ポリゴンメッシュ (GLB) を生成。
   - Unity/PlayCanvas への自動アップロード転送機能。

2. **SpeedySplat 3DGS 自動学習 (Docker連携)**
   - RealityScan の処理と並行して、裏側（Docker コンテナ）で COLMAP と **SpeedySplat** による最新・爆速の 3DGS 学習を実行。
   - 環境を汚さずに安定して CUDA 依存の学習タスクを回せます。

3. **SuperSplat ビューワー統合**
   - 出力された 3DGS モデル（`.ply`）を、WebUI 上に埋め込まれた SuperSplat 高機能エディタにドラッグ＆ドロップして即座にプレビュー・編集可能。

---

## 🛠️ 事前準備（前提条件）

このシステムを動かすには以下の環境が必要です。

- **OS**: Windows 10/11
- **GPU**: NVIDIA GPU (推奨: VRAM 12GB以上)
- **ソフトウェア**:
  1. [Python 3.10+](https://www.python.org/downloads/)
  2. [Docker Desktop](https://www.docker.com/products/docker-desktop/) (WSL2バックエンド有効化済)
  3. [RealityScan 2.0 CLI](https://www.capturingreality.com/realityscan-cli)
  4. FFmpeg (動画から画像を描画するために必要。環境変数パスが通っているか、`C:\ffmpeg\bin\ffmpeg.exe` 等に配置)

---

## 🚀 初回セットアップ

1. **Python パッケージのインストール**
   コマンドプロンプトや PowerShell を開き、プロジェクトフォルダに移動して以下を実行します。

   ```bash
   cd D:\RealityScanWebUI
   pip install gradio requests pygltflib
   ```

2. **Docker イメージのビルド (3DGS用)**
   SpeedySplat と COLMAP を含んだ学習専用のコンテナ環境をビルドします。（初回のみ数分〜十数分かかります）

   ```bash
   docker-compose build
   ```

---

## 💻 起動方法

普段使用する際は、以下のコマンド1つで WebUI が立ち上がります。

```bash
cd D:\RealityScanWebUI
python app.py
```

ターミナルに `Running on local URL:  http://127.0.0.1:xxxx` と表示されたら、ブラウザでその URL を開いてください。

---

## 📖 ユーザー向け操作マニュアル

### 1. データの入力と設定

- **ファイルアップロード**: スキャンしたい動画（.mp4 等）または画像セット（.jpg, .png の複数選択可）をドロップします。動画の場合は「動画抽出FPS」スライダーで1秒間あたり何枚の画像を切り出すか調整できます。
- **モデル名**: `apple01` のような半角英数でプロジェクト名を入力します。出力ファイル名に利用されます。
- **メッシュ品質**: 用途に合わせて選択してください。（ノーマル・プレビュー・最高品質など）

### 2. RealityScan 新機能と 3DGS 設定

- **🔥 SpeedySplat (3DGS) 同時学習**: ここにチェックを入れると、GLB（ポリゴン）生成の裏側で Docker コンテナが起動し、3DGSの学習モデル (.ply) を自動生成します。
- **マスク・広域モード**: AIによる不要領域の除外や、複数スキャンの結合を行う設定です。

### 3. ポタンを押して処理開始

- 「**3Dモデル・3DGS変換を開始**」ボタンを押します。
- 進捗バーに現在の進行状況が表示されます。
- GLBモデルの生成が完了すると、自動的に「プレビュー」画面に 3D モデルが表示されます。

### 4. 3DGS / PLY ビューワーの使用方法

- RealityScan の処理が終わっても、3DGSの学習はバックグラウンド（Docker）で続いている場合があります。
- 「**3DGS / PLY ビューワー (SpeedySplat)**」タブを開き、「**最新の学習状況を確認 / モデル読込**」ボタンを押してください。
- `✅ 学習完了！ PLYファイル出力済み:` と表示されれば完了です。
- タブ内の「**学習済 Splat PLY**」ファイルを自分のPC（デスクトップなど）にドラッグして保存し、そのすぐ下にある **SuperSplat ビューワーの画面内へそのままドラッグ＆ドロップ** すると、3DGSをグリグリ動かしてプレビューできます！

### 5. Unity / PlayCanvas への転送

- 画面一番下の「送信先」セクションから、Unityの特定フォルダや、PlayCanvasのプロジェクトへ GLB データを自動アップロードできます。

---

## ⚠️ トラブルシューティング

- **Q. Docker でのエラーや学習が始まらない**
  - Docker Desktop が起動していることを確認してください。
  - `docker-compose up speedysplat` を手動実行することで詳細なエラーログを確認できます。
- **Q. RealityScanCLIコマンドが見つからない**
  - `app.py` 冒頭の `REALITYSCAN_PATH` がご使用のPCにおける RealityScan バッチファイルの正しいパスを指しているか確認してください。
- **Q. GLBが真上を向いている**
  - 現バージョンで自動修正（X軸-90度回転およびテクスチャ埋め込み）が入っているため、基本的には正面を向きます。表示がおかしい場合は、もう一度エクスポートを試してください。
