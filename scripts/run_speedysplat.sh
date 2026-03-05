#!/bin/bash
# ===================================================================
# FastGS 3DGS 高速学習スクリプト
# CVPR 2025: "Training 3D Gaussian Splatting in 100 Seconds"
#
# 使い方: bash run_speedysplat.sh <project_name>
#
# COLMAPスキップモード:
#   /workspace/data/output/<project>_colmap/sparse/0/ に
#   cameras.txt, images.txt, points3D.txt があり、
#   /workspace/data/output/<project>_colmap/images/ に
#   歪み補正済み画像があれば、COLMAP処理を省略して直接学習する。
# ===================================================================

set -e

PROJECT_NAME=${1:-"default"}
INPUT_DIR="/workspace/data/input"
OUTPUT_DIR="/workspace/data/output"
WORK_DIR="/workspace/data/working/${PROJECT_NAME}"
FASTGS_DIR="/workspace/fastgs"
COLMAP_PRECOMPUTED="/workspace/data/output/${PROJECT_NAME}_colmap"

echo "=== FastGS 3DGS Training: ${PROJECT_NAME} ==="
echo "Input: ${INPUT_DIR}"
echo "Output: ${OUTPUT_DIR}"

mkdir -p "${WORK_DIR}"
mkdir -p "${OUTPUT_DIR}"

# ===================================================================
# COLMAPスキップ判定: RealityScanからのCOLMAPエクスポートがあるか確認
# ===================================================================
COLMAP_SKIP=false

if [ -d "${COLMAP_PRECOMPUTED}/sparse/0" ] && [ -d "${COLMAP_PRECOMPUTED}/images" ]; then
    # sparse/0 に cameras.txt または cameras.bin が存在するか
    if ls "${COLMAP_PRECOMPUTED}/sparse/0/"cameras.* 1>/dev/null 2>&1; then
        COLMAP_SKIP=true
    fi
fi

if [ "${COLMAP_SKIP}" = true ]; then
    # =============================================================
    # モードA: COLMAPスキップ（RealityScan COLMAP出力を直接利用）
    # =============================================================
    echo "=============================================="
    echo "  COLMAPスキップモード"
    echo "  RealityScan COLMAP出力を直接利用します"
    echo "=============================================="

    echo "[1/3] RealityScan COLMAP データをコピー中..."
    cp -r "${COLMAP_PRECOMPUTED}/sparse" "${WORK_DIR}/"
    cp -r "${COLMAP_PRECOMPUTED}/images" "${WORK_DIR}/"

    SPARSE_FILES=$(ls -1 "${WORK_DIR}/sparse/0/" 2>/dev/null | wc -l)
    IMAGE_COUNT=$(ls -1 "${WORK_DIR}/images/" 2>/dev/null | wc -l)
    echo "  sparse/0: ${SPARSE_FILES} files"
    echo "  images: ${IMAGE_COUNT} files"
    echo "  COLMAP処理をスキップしました (推定 2時間+ の時間短縮)"

    # FastGS 高速学習
    echo "[2/3] FastGS 高速学習開始..."
    cd "${FASTGS_DIR}"
    python train.py \
        -s "${WORK_DIR}" \
        -m "${WORK_DIR}/output" \
        --densification_interval 500 \
        --optimizer_type default \
        --test_iterations 30000

    echo "  FastGS 学習完了!"

    # 出力PLYをコピー
    echo "[3/3] 出力ファイルを確認中..."

else
    # =============================================================
    # モードB: 従来フロー（COLMAP → FastGS）
    # =============================================================
    echo "=============================================="
    echo "  従来モード（COLMAP + FastGS）"
    echo "=============================================="

    # Step 1: 画像コピー
    mkdir -p "${WORK_DIR}/input"
    echo "[1/4] 画像を作業ディレクトリにコピー中..."
    cp ${INPUT_DIR}/*.jpg "${WORK_DIR}/input/" 2>/dev/null || true
    cp ${INPUT_DIR}/*.png "${WORK_DIR}/input/" 2>/dev/null || true
    cp ${INPUT_DIR}/*.jpeg "${WORK_DIR}/input/" 2>/dev/null || true

    IMAGE_COUNT=$(ls -1 "${WORK_DIR}/input/" 2>/dev/null | wc -l)
    echo "  画像枚数: ${IMAGE_COUNT}"

    if [ "${IMAGE_COUNT}" -lt 3 ]; then
        echo "ERROR: 入力画像が3枚未満です。3DGS学習にはより多くの画像が必要です。"
        exit 1
    fi

    # Step 2: COLMAP でカメラポーズを推定
    echo "[2/4] COLMAP でカメラポーズ推定中（SfM）..."
    cd "${FASTGS_DIR}"

    # COLMAP を直接実行（convert.py は sequential_matcher 非対応のため）
    # 動画フレーム用: sequential_matcher + 単一カメラモデル (OPENCV)
    COLMAP_DB="${WORK_DIR}/distorted/database.db"
    COLMAP_IMG="${WORK_DIR}/input"
    COLMAP_SPARSE_DISTORTED="${WORK_DIR}/distorted/sparse"
    mkdir -p "${WORK_DIR}/distorted"
    mkdir -p "${COLMAP_SPARSE_DISTORTED}"

    echo "  [2a] 特徴点抽出中..."
    colmap feature_extractor \
        --database_path "${COLMAP_DB}" \
        --image_path "${COLMAP_IMG}" \
        --ImageReader.single_camera 1 \
        --ImageReader.camera_model OPENCV \
        --SiftExtraction.use_gpu 1

    echo "  [2b] シーケンシャルマッチング中..."
    colmap sequential_matcher \
        --database_path "${COLMAP_DB}" \
        --SiftMatching.use_gpu 1

    echo "  [2c] SfM マッピング中..."
    colmap mapper \
        --database_path "${COLMAP_DB}" \
        --image_path "${COLMAP_IMG}" \
        --output_path "${COLMAP_SPARSE_DISTORTED}"

    echo "  [2d] 歪み補正中..."
    colmap image_undistorter \
        --image_path "${COLMAP_IMG}" \
        --input_path "${COLMAP_SPARSE_DISTORTED}/0" \
        --output_path "${WORK_DIR}" \
        --output_type COLMAP

    # COLMAP出力の検証 (image_undistorter は sparse/ に直接出力する場合がある)
    # FastGS の train.py は sparse/0/ を期待するため、必要に応じてリネーム
    if [ ! -d "${WORK_DIR}/sparse/0" ]; then
        # sparse/ に直接 .bin/.txt ファイルがある場合 → sparse/0/ に移動
        if ls "${WORK_DIR}/sparse/"*.bin 1>/dev/null 2>&1 || ls "${WORK_DIR}/sparse/"*.txt 1>/dev/null 2>&1; then
            echo "  sparse/ → sparse/0/ に構造変換中..."
            mkdir -p "${WORK_DIR}/sparse/0_tmp"
            mv "${WORK_DIR}/sparse/"*.bin "${WORK_DIR}/sparse/0_tmp/" 2>/dev/null || true
            mv "${WORK_DIR}/sparse/"*.txt "${WORK_DIR}/sparse/0_tmp/" 2>/dev/null || true
            mv "${WORK_DIR}/sparse/0_tmp" "${WORK_DIR}/sparse/0"
        else
            echo "ERROR: COLMAP再構築に失敗しました。sparse ディレクトリにデータがありません。"
            echo "  考えられる原因:"
            echo "  - 画像間のオーバーラップが不十分"
            echo "  - 画像枚数が少なすぎる (推奨: 20枚以上)"
            echo "  - 画像の品質が低い（ブレ、低解像度等）"
            echo ""
            echo "  作業ディレクトリの内容:"
            ls -la "${WORK_DIR}/" 2>/dev/null
            echo "  sparse/の内容:"
            ls -la "${WORK_DIR}/sparse/" 2>/dev/null || echo "  (sparse/ なし)"
            exit 1
        fi
    fi

    SPARSE_FILES=$(ls -1 "${WORK_DIR}/sparse/0/" 2>/dev/null | wc -l)
    echo "  COLMAP 完了 (sparse/0: ${SPARSE_FILES} files)"

    # Step 3: FastGS 高速学習
    echo "[3/4] FastGS 高速学習開始..."
    python train.py \
        -s "${WORK_DIR}" \
        -m "${WORK_DIR}/output" \
        --densification_interval 500 \
        --optimizer_type default \
        --test_iterations 30000

    echo "  FastGS 学習完了!"

    # Step 4: 出力PLY
    echo "[4/4] 出力ファイルを確認中..."
fi

# ===================================================================
# 共通: 出力されたPLYをコピー
# ===================================================================
RESULT_PLY="${WORK_DIR}/output/point_cloud/iteration_30000/point_cloud.ply"
if [ ! -f "${RESULT_PLY}" ]; then
    RESULT_PLY=$(find "${WORK_DIR}/output" -name "point_cloud.ply" -type f | sort | tail -1)
fi

if [ -n "${RESULT_PLY}" ] && [ -f "${RESULT_PLY}" ]; then
    cp "${RESULT_PLY}" "${OUTPUT_DIR}/${PROJECT_NAME}_3dgs.ply"

    # X軸-90度回転（GLBと同じ座標系にする）
    echo "  PLY X軸-90度回転中..."
    python /workspace/scripts/rotate_ply.py "${OUTPUT_DIR}/${PROJECT_NAME}_3dgs.ply"

    PLY_SIZE=$(du -h "${OUTPUT_DIR}/${PROJECT_NAME}_3dgs.ply" | cut -f1)
    echo "=== 完了! 出力: ${OUTPUT_DIR}/${PROJECT_NAME}_3dgs.ply (${PLY_SIZE}) ==="
else
    echo "ERROR: PLYファイルが見つかりません。学習に失敗した可能性があります。"
    ls -la "${WORK_DIR}/output/" 2>/dev/null || echo "出力ディレクトリがありません"
    exit 1
fi
