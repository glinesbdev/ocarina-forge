namespace OcarinaForge.Import;

/// <summary>
/// Bone bind pose. Skin 0 is in the shape bone's local space. Smooth skin (2–4) is already in model space.
/// Euler flag 0x1000 is extrinsic XYZ, the same composition as a banc Rotate.
/// </summary>
public static class BindPose
{
    public readonly record struct Bone(int Parent, double Px, double Py, double Pz, double Rx, double Ry, double Rz, double Rw, double Sx, double Sy, double Sz, uint Flags, int Rigid);

    public static double[] Matrix(double x, double y, double z, double rx, double ry, double rz, double sx, double sy, double sz)
    {
        var rot = Euler(rx, ry, rz);
        return Trs(x, y, z, rot.X, rot.Y, rot.Z, rot.W, sx, sy, sz);
    }

    public static (double X, double Y, double Z, double W) Euler(double x, double y, double z)
    {
        double cx = Math.Cos(x * 0.5), sx = Math.Sin(x * 0.5);
        double cy = Math.Cos(y * 0.5), sy = Math.Sin(y * 0.5);
        double cz = Math.Cos(z * 0.5), sz = Math.Sin(z * 0.5);
        return (
            sx * cy * cz - cx * sy * sz,
            cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz,
            cx * cy * cz + sx * sy * sz);
    }

    /// <summary>
    /// <c>T · inverse(parent scale) · R · S</c> when <paramref name="compensate"/> is set.
    /// Segment scale compensation (bone flag <c>0x800000</c>) keeps a parent's scale from shearing the child.
    /// </summary>
    public static double[] Compensate(double px, double py, double pz, double rx, double ry, double rz, double rw, double sx, double sy, double sz, double parentSx, double parentSy, double parentSz, bool compensate)
    {
        if (!compensate) return Trs(px, py, pz, rx, ry, rz, rw, sx, sy, sz);
        var spin = Mul(ScaleInverse(parentSx, parentSy, parentSz), Trs(0, 0, 0, rx, ry, rz, rw, sx, sy, sz));
        return Mul(Trs(px, py, pz, 0, 0, 0, 1, 1, 1, 1), spin);
    }

    public static double[] Multiply(double[] left, double[] right) => Mul(left, right);

    public static double[] Invert(double[] m)
    {
        double a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3];
        double a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7];
        double a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11];
        double c00 = a11 * a22 - a12 * a21;
        double c01 = a12 * a20 - a10 * a22;
        double c02 = a10 * a21 - a11 * a20;
        double c10 = a02 * a21 - a01 * a22;
        double c11 = a00 * a22 - a02 * a20;
        double c12 = a01 * a20 - a00 * a21;
        double c20 = a01 * a12 - a02 * a11;
        double c21 = a02 * a10 - a00 * a12;
        double c22 = a00 * a11 - a01 * a10;
        double det = a00 * c00 + a01 * c01 + a02 * c02;
        if (Math.Abs(det) < 1e-12) return Trs(0, 0, 0, 0, 0, 0, 1, 1, 1, 1);
        double inv = 1.0 / det;
        double i00 = c00 * inv, i01 = c10 * inv, i02 = c20 * inv;
        double i10 = c01 * inv, i11 = c11 * inv, i12 = c21 * inv;
        double i20 = c02 * inv, i21 = c12 * inv, i22 = c22 * inv;
        return new[]
        {
            i00, i01, i02, -(i00 * a03 + i01 * a13 + i02 * a23),
            i10, i11, i12, -(i10 * a03 + i11 * a13 + i12 * a23),
            i20, i21, i22, -(i20 * a03 + i21 * a13 + i22 * a23),
            0, 0, 0, 1,
        };
    }

    public readonly record struct Decomposed(double Px, double Py, double Pz, double Rx, double Ry, double Rz, double Rw, double Sx, double Sy, double Sz);

    public static Decomposed Decompose(double[] m)
    {
        double px = m[3], py = m[7], pz = m[11];
        double sx = Math.Sqrt(m[0] * m[0] + m[4] * m[4] + m[8] * m[8]);
        double sy = Math.Sqrt(m[1] * m[1] + m[5] * m[5] + m[9] * m[9]);
        double sz = Math.Sqrt(m[2] * m[2] + m[6] * m[6] + m[10] * m[10]);
        double det = m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8]) + m[2] * (m[4] * m[9] - m[5] * m[8]);
        if (det < 0) sx = -sx;
        if (Math.Abs(sx) < 1e-8) sx = 1;
        if (Math.Abs(sy) < 1e-8) sy = 1;
        if (Math.Abs(sz) < 1e-8) sz = 1;
        double r00 = m[0] / sx, r10 = m[4] / sx, r20 = m[8] / sx;
        double r01 = m[1] / sy, r11 = m[5] / sy, r21 = m[9] / sy;
        double r02 = m[2] / sz, r12 = m[6] / sz, r22 = m[10] / sz;
        double trace = r00 + r11 + r22;
        double rx, ry, rz, rw;
        if (trace > 0)
        {
            double s = Math.Sqrt(trace + 1.0) * 2;
            rw = 0.25 * s;
            rx = (r21 - r12) / s;
            ry = (r02 - r20) / s;
            rz = (r10 - r01) / s;
        }
        else if (r00 > r11 && r00 > r22)
        {
            double s = Math.Sqrt(1.0 + r00 - r11 - r22) * 2;
            rw = (r21 - r12) / s;
            rx = 0.25 * s;
            ry = (r01 + r10) / s;
            rz = (r02 + r20) / s;
        }
        else if (r11 > r22)
        {
            double s = Math.Sqrt(1.0 + r11 - r00 - r22) * 2;
            rw = (r02 - r20) / s;
            rx = (r01 + r10) / s;
            ry = 0.25 * s;
            rz = (r12 + r21) / s;
        }
        else
        {
            double s = Math.Sqrt(1.0 + r22 - r00 - r11) * 2;
            rw = (r10 - r01) / s;
            rx = (r02 + r20) / s;
            ry = (r12 + r21) / s;
            rz = 0.25 * s;
        }
        double n = Math.Sqrt(rx * rx + ry * ry + rz * rz + rw * rw);
        if (n < 1e-8) { rx = ry = rz = 0; rw = 1; }
        else { rx /= n; ry /= n; rz /= n; rw /= n; }
        return new Decomposed(px, py, pz, rx, ry, rz, rw, sx, sy, sz);
    }

    public static double[] Trs(double px, double py, double pz, double rx, double ry, double rz, double rw, double sx, double sy, double sz)
    {
        double n = rx * rx + ry * ry + rz * rz + rw * rw;
        if (n < 1e-8)
        {
            rx = ry = rz = 0;
            rw = 1;
            n = 1;
        }
        double s = 2.0 / n;
        double xx = rx * rx * s, yy = ry * ry * s, zz = rz * rz * s;
        double xy = rx * ry * s, xz = rx * rz * s, yz = ry * rz * s;
        double wx = rw * rx * s, wy = rw * ry * s, wz = rw * rz * s;
        return new[]
        {
            (1 - yy - zz) * sx, (xy - wz) * sy, (xz + wy) * sz, px,
            (xy + wz) * sx, (1 - xx - zz) * sy, (yz - wx) * sz, py,
            (xz - wy) * sx, (yz + wx) * sy, (1 - xx - yy) * sz, pz,
            0, 0, 0, 1,
        };
    }

    public static (double X, double Y, double Z) Apply(double[] matrix, double x, double y, double z) => (
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11]);

    public static List<double[]> Worlds(IReadOnlyList<Bone> bones)
    {
        var worlds = new double[]?[bones.Count];
        var pending = new HashSet<int>(Enumerable.Range(0, bones.Count));
        for (int pass = 0; pass < bones.Count + 1 && pending.Count > 0; pass++)
        {
            var ready = new List<int>();
            foreach (int index in pending)
            {
                var bone = bones[index];
                var spinning = Trs(0, 0, 0, bone.Rx, bone.Ry, bone.Rz, bone.Rw, bone.Sx, bone.Sy, bone.Sz);
                if ((bone.Flags & 0x800000) != 0 && bone.Parent >= 0 && bone.Parent < bones.Count && bone.Parent != index)
                    spinning = Mul(ScaleInverse(bones[bone.Parent]), spinning);
                var local = Mul(Trs(bone.Px, bone.Py, bone.Pz, 0, 0, 0, 1, 1, 1, 1), spinning);
                if (bone.Parent == index || bone.Parent < 0 || bone.Parent >= bones.Count)
                {
                    worlds[index] = local;
                    ready.Add(index);
                }
                else if (worlds[bone.Parent] is not null)
                {
                    worlds[index] = Mul(worlds[bone.Parent]!, local);
                    ready.Add(index);
                }
            }
            if (ready.Count == 0) break;
            foreach (int index in ready) pending.Remove(index);
        }
        for (int index = 0; index < bones.Count; index++)
        {
            if (worlds[index] is not null) continue;
            var bone = bones[index];
            worlds[index] = Trs(bone.Px, bone.Py, bone.Pz, bone.Rx, bone.Ry, bone.Rz, bone.Rw, bone.Sx, bone.Sy, bone.Sz);
        }
        return worlds.Select(item => item!).ToList();
    }

    public static void Bake(List<(double X, double Y, double Z)> positions, IReadOnlyList<Bone> bones, IReadOnlyList<int> palette, int boneIndex, IReadOnlyList<int>? slots)
    {
        if (bones.Count == 0 || positions.Count == 0) return;
        var worlds = Worlds(bones);
        double[]? MatrixFor(int? slot)
        {
            int index = boneIndex;
            if (slot is int chosen)
            {
                if (palette.Count > 0 && chosen >= 0 && chosen < palette.Count) index = palette[chosen];
                else if (chosen >= 0 && chosen < bones.Count) index = chosen;
            }
            if (index < 0 || index >= bones.Count || index >= worlds.Count) return null;
            if (slot is not null && bones[index].Rigid < 0) return null;
            return worlds[index];
        }
        if (slots is null)
        {
            var matrix = MatrixFor(null);
            if (matrix is null) return;
            for (int i = 0; i < positions.Count; i++)
                positions[i] = Apply(matrix, positions[i].X, positions[i].Y, positions[i].Z);
            return;
        }
        for (int i = 0; i < positions.Count; i++)
        {
            var matrix = MatrixFor(i < slots.Count ? slots[i] : null);
            if (matrix is not null)
                positions[i] = Apply(matrix, positions[i].X, positions[i].Y, positions[i].Z);
        }
    }

    /// <summary>
    /// Rotate normals with the same bone as <see cref="Bake"/>. The 3×3 is the inverse transpose,
    /// which is what <c>_rigid_normals</c> applies, so a scaled bone does not shear the light.
    /// A null matrix (a skin-1 bone with no rigid slot) leaves that normal where it is.
    /// </summary>
    public static void BakeNormals(List<Vec3> normals, IReadOnlyList<Bone> bones, IReadOnlyList<int> palette, int boneIndex, IReadOnlyList<int>? slots)
    {
        if (bones.Count == 0 || normals.Count == 0) return;
        var worlds = Worlds(bones);
        double[]? MatrixFor(int? slot)
        {
            int index = boneIndex;
            if (slot is int chosen)
            {
                if (palette.Count > 0 && chosen >= 0 && chosen < palette.Count) index = palette[chosen];
                else if (chosen >= 0 && chosen < bones.Count) index = chosen;
            }
            if (index < 0 || index >= bones.Count || index >= worlds.Count) return null;
            if (slot is not null && bones[index].Rigid < 0) return null;
            return worlds[index];
        }
        void One(int i, double[]? matrix)
        {
            if (matrix is null) return;
            if (TransformNormal(matrix, normals[i]) is { } turned) normals[i] = turned;
        }
        if (slots is null)
        {
            var matrix = MatrixFor(null);
            for (int i = 0; i < normals.Count; i++) One(i, matrix);
            return;
        }
        for (int i = 0; i < normals.Count; i++)
            One(i, MatrixFor(i < slots.Count ? slots[i] : null));
    }

    static Vec3? TransformNormal(double[] matrix, Vec3 normal)
    {
        double a0 = matrix[0], a1 = matrix[4], a2 = matrix[8];
        double b0 = matrix[1], b1 = matrix[5], b2 = matrix[9];
        double c0 = matrix[2], c1 = matrix[6], c2 = matrix[10];
        static (double X, double Y, double Z) Cross((double X, double Y, double Z) u, (double X, double Y, double Z) v) =>
            (u.Y * v.Z - u.Z * v.Y, u.Z * v.X - u.X * v.Z, u.X * v.Y - u.Y * v.X);
        var ca = Cross((b0, b1, b2), (c0, c1, c2));
        var cb = Cross((c0, c1, c2), (a0, a1, a2));
        var cc = Cross((a0, a1, a2), (b0, b1, b2));
        double det = a0 * ca.X + a1 * ca.Y + a2 * ca.Z;
        if (Math.Abs(det) < 1e-12) return null;
        double dx = ca.X * normal.X + cb.X * normal.Y + cc.X * normal.Z;
        double dy = ca.Y * normal.X + cb.Y * normal.Y + cc.Y * normal.Z;
        double dz = ca.Z * normal.X + cb.Z * normal.Y + cc.Z * normal.Z;
        double length = Math.CopySign(Math.Sqrt(dx * dx + dy * dy + dz * dz), det);
        if (Math.Abs(length) < 1e-12) return null;
        return new Vec3(dx / length, dy / length, dz / length);
    }

    static double[] ScaleInverse(Bone bone) => ScaleInverse(bone.Sx, bone.Sy, bone.Sz);

    static double[] ScaleInverse(double sx, double sy, double sz)
    {
        static double Inv(double value) => Math.Abs(value) > 1e-8 ? 1.0 / value : 1.0;
        return new[]
        {
            Inv(sx), 0, 0, 0,
            0, Inv(sy), 0, 0,
            0, 0, Inv(sz), 0,
            0, 0, 0, 1,
        };
    }

    static double[] Mul(double[] a, double[] b)
    {
        var output = new double[16];
        for (int row = 0; row < 4; row++)
        for (int col = 0; col < 4; col++)
        {
            double sum = 0;
            for (int k = 0; k < 4; k++) sum += a[row * 4 + k] * b[k * 4 + col];
            output[row * 4 + col] = sum;
        }
        return output;
    }
}
