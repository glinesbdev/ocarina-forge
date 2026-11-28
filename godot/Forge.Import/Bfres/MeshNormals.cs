namespace OcarinaForge.Import;

/// <summary>Smooth normals from a triangle list, used when a shape has no <c>_n0</c>.</summary>
public static class MeshNormals
{
    public static List<Vec3> Generate(IReadOnlyList<Vec3> positions, IReadOnlyList<int> indices)
    {
        var acc = new double[positions.Count * 3];
        for (int i = 0; i + 2 < indices.Count; i += 3)
        {
            int a = indices[i];
            int b = indices[i + 1];
            int c = indices[i + 2];
            if ((uint)a >= (uint)positions.Count || (uint)b >= (uint)positions.Count || (uint)c >= (uint)positions.Count)
                continue;
            var p0 = positions[a];
            var p1 = positions[b];
            var p2 = positions[c];
            double ux = p1.X - p0.X, uy = p1.Y - p0.Y, uz = p1.Z - p0.Z;
            double vx = p2.X - p0.X, vy = p2.Y - p0.Y, vz = p2.Z - p0.Z;
            double nx = uy * vz - uz * vy;
            double ny = uz * vx - ux * vz;
            double nz = ux * vy - uy * vx;
            Add(acc, a, nx, ny, nz);
            Add(acc, b, nx, ny, nz);
            Add(acc, c, nx, ny, nz);
        }
        var normals = new List<Vec3>(positions.Count);
        for (int i = 0; i < positions.Count; i++)
        {
            double x = acc[i * 3], y = acc[i * 3 + 1], z = acc[i * 3 + 2];
            double length = Math.Sqrt(x * x + y * y + z * z);
            normals.Add(length < 1e-12 ? new Vec3(0, 1, 0) : new Vec3(x / length, y / length, z / length));
        }
        return normals;
    }

    static void Add(double[] acc, int index, double x, double y, double z)
    {
        acc[index * 3] += x;
        acc[index * 3 + 1] += y;
        acc[index * 3 + 2] += z;
    }
}
