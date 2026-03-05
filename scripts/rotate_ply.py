"""
rotate_ply.py — 3DGS PLY ファイルを X軸 -90度回転

RealityScan/COLMAP の座標系（Y-up）を
一般的な3Dビューワーの座標系に合わせるために使用。
"""

import sys
import struct
import numpy as np
from pathlib import Path


def read_ply_header(f):
    """PLYヘッダーを読み取り、プロパティ情報を返す"""
    header_lines = []
    properties = []
    vertex_count = 0

    while True:
        line = f.readline().decode('ascii').strip()
        header_lines.append(line)
        if line.startswith('element vertex'):
            vertex_count = int(line.split()[-1])
        elif line.startswith('property'):
            parts = line.split()
            properties.append({'type': parts[1], 'name': parts[2]})
        elif line == 'end_header':
            break

    return header_lines, properties, vertex_count


def ply_type_to_numpy(t):
    """PLY型をnumpy型に変換"""
    mapping = {
        'float': np.float32,
        'double': np.float64,
        'int': np.int32,
        'uint': np.uint32,
        'short': np.int16,
        'ushort': np.uint16,
        'char': np.int8,
        'uchar': np.uint8,
    }
    return mapping.get(t, np.float32)


def quaternion_multiply(q1, q2):
    """四元数の乗算 q1 * q2"""
    w1, x1, y1, z1 = q1[:, 0], q1[:, 1], q1[:, 2], q1[:, 3]
    w2, x2, y2, z2 = q2[0], q2[1], q2[2], q2[3]

    w = w1*w2 - x1*x2 - y1*y2 - z1*z2
    x = w1*x2 + x1*w2 + y1*z2 - z1*y2
    y = w1*y2 - x1*z2 + y1*w2 + z1*x2
    z = w1*z2 + x1*y2 - y1*x2 + z1*w2

    return np.stack([w, x, y, z], axis=-1)


def rotate_ply_x90(input_path, output_path=None):
    """PLYファイルをX軸-90度回転"""
    if output_path is None:
        output_path = input_path

    input_path = Path(input_path)
    output_path = Path(output_path)

    print(f"  PLY回転: {input_path.name}")

    with open(input_path, 'rb') as f:
        header_lines, properties, vertex_count = read_ply_header(f)
        prop_names = [p['name'] for p in properties]
        prop_types = [p['type'] for p in properties]

        # バイナリデータ読み取り
        dtype = np.dtype([(p['name'], ply_type_to_numpy(p['type'])) for p in properties])
        data = np.frombuffer(f.read(), dtype=dtype, count=vertex_count)

    # 座標を取得して回転
    # X軸-90度回転: new_y = z, new_z = -y
    x = data['x'].copy()
    y = data['y'].copy()
    z = data['z'].copy()

    new_x = x
    new_y = z
    new_z = -y

    # 法線も回転（存在する場合）
    has_normals = 'nx' in prop_names and 'ny' in prop_names and 'nz' in prop_names

    # 回転四元数（X軸-90度）
    # q = [cos(-45°), sin(-45°), 0, 0] = [√2/2, -√2/2, 0, 0]
    angle = -np.pi / 2
    q_rot = np.array([np.cos(angle / 2), np.sin(angle / 2), 0, 0], dtype=np.float32)

    # 四元数回転（rot_0〜rot_3 が存在する場合）
    has_rot = 'rot_0' in prop_names

    # 書き込み用データ作成
    new_data = np.copy(data)
    new_data['x'] = new_x
    new_data['y'] = new_y
    new_data['z'] = new_z

    if has_normals:
        nx = data['nx'].copy()
        ny = data['ny'].copy()
        nz = data['nz'].copy()
        new_data['nx'] = nx
        new_data['ny'] = nz
        new_data['nz'] = -ny

    if has_rot:
        quats = np.stack([
            data['rot_0'], data['rot_1'],
            data['rot_2'], data['rot_3']
        ], axis=-1).astype(np.float32)

        rotated_quats = quaternion_multiply(quats, q_rot)
        # 正規化
        norms = np.linalg.norm(rotated_quats, axis=-1, keepdims=True)
        rotated_quats = rotated_quats / (norms + 1e-8)

        new_data['rot_0'] = rotated_quats[:, 0]
        new_data['rot_1'] = rotated_quats[:, 1]
        new_data['rot_2'] = rotated_quats[:, 2]
        new_data['rot_3'] = rotated_quats[:, 3]

    # 書き出し
    header = '\n'.join(header_lines) + '\n'
    with open(output_path, 'wb') as f:
        f.write(header.encode('ascii'))
        f.write(new_data.tobytes())

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  PLY回転完了: {output_path.name} ({size_mb:.1f} MB)")
    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python rotate_ply.py <input.ply> [output.ply]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file
    rotate_ply_x90(input_file, output_file)
