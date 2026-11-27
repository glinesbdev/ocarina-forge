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

    static double[] ScaleInverse(Bone bone)
    {
        static double Inv(double value) => Math.Abs(value) > 1e-8 ? 1.0 / value : 1.0;
        return new[]
        {
            Inv(bone.Sx), 0, 0, 0,
            0, Inv(bone.Sy), 0, 0,
            0, 0, Inv(bone.Sz), 0,
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
