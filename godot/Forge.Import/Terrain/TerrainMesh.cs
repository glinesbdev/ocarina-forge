namespace OcarinaForge.Import;

/// <summary>
/// TerrainArc height meshes. Layout matches <c>ocarina_forge/formats/terrain.py</c>:
/// TSCB scale, tile table, TARB/zstd <c>.hght</c>, 257×257 float samples, and the
/// column-256 dashed-zero repair. Material atlases are not decoded.
/// </summary>
public static class TerrainMesh
{
    public const int Size = 257;
    const int HeightLog2 = 4;
    const double ClipPad = 200;
    const double NearM = 70;
    const double MidM = 180;
    const double SkirtM = 4;
    static readonly int[] Steps = { 8, 16, 32, 64 };

    public sealed class Tile
    {
        public string Name { get; init; } = "";
        public double CenterX { get; init; }
        public double CenterZ { get; init; }
        public double Edge { get; init; }
        public float[] Heights { get; init; } = Array.Empty<float>();
        /// <summary>MaterialAlb index per height sample, or null. -1 is the flat earth colour.</summary>
        public int[]? Layers { get; init; }
    }

    public static (double Scale, double HeightScale, List<(double X, double Z, double Edge, string Name)> Tiles) ReadTscb(byte[] data)
    {
        if (data.Length < 0x80 || data[0] != (byte)'T' || data[1] != (byte)'S')
            throw new ImportException("not a TSCB terrain index");
        double scale = Bin.F32(data, 0x10);
        double height = Bin.F32(data, 0x14);
        int count = (int)Bin.U32(data, 0x38);
        if (count is <= 0 or > 200000) throw new ImportException($"TSCB tile count {count} is not usable");
        if (scale <= 0 || height == 0) throw new ImportException($"TSCB scales {scale} and {height} are not usable");
        int table = Bin.Rel32(data, 0x78);
        var tiles = new List<(double, double, double, string)>();
        for (int index = 0; index < count; index++)
        {
            int slot = table + index * 4;
            if (slot + 4 > data.Length) break;
            int at = Bin.Rel32(data, slot);
            if (at + 0x64 > data.Length) continue;
            double cx = Bin.F32(data, at);
            double cz = Bin.F32(data, at + 4);
            double edge = Bin.F32(data, at + 8);
            if (edge <= 0) continue;
            int nameAt = Bin.Rel32(data, at + 0x60);
            string name = Bin.CString(data, nameAt, 80).Trim();
            if (name.Length > 0) tiles.Add((cx, cz, edge, name));
        }
        if (tiles.Count == 0) throw new ImportException("TSCB has no named tiles");
        return (scale, height, tiles);
    }

    public static byte[] UnpackHght(byte[] data, ZstdDictionaries? dictionaries)
    {
        if (data.Length >= 4 && data[0] == (byte)'T' && data[1] == (byte)'A' && data[2] == (byte)'R' && data[3] == (byte)'B')
        {
            if (data.Length < 12) throw new ImportException("TARB header is truncated");
            int header = Bin.U16(data, 10);
            if (header < 12 || header >= data.Length)
                throw new ImportException($"TARB header size {header} does not leave a zstd frame");
            data = data.AsSpan(header).ToArray();
        }
        try { return ZstdFrames.Decompress(data, dictionaries); }
        catch (ImportException exc) { throw new ImportException($"heightmap did not decompress: {exc.Message}", exc); }
    }

    public static float[] DecodeHeights(byte[] payload)
    {
        var block = Block(payload, 0x2C);
        if (block is null) throw new ImportException("height block is missing");
        var values = AsFloats(block);
        if (values is null) throw new ImportException("height block is not a 257×257 grid");
        return values;
    }

    /// <summary>
    /// One MaterialAlb index per sample. Retail <c>.mate</c> is the same TARB + zstd wrapper as
    /// <c>.hght</c>. The image is 257×257 texels of material A, material B, blend, extra.
    /// 255 and 127 are not layer indices. Blend at or above 128 selects material B; otherwise A.
    /// A halfway 0x7f blend still uses A here. Returns null when the block is not that grid.
    /// </summary>
    public static int[]? ReadMate(byte[] file, ZstdDictionaries? dictionaries)
    {
        byte[] payload;
        try { payload = UnpackHght(file, dictionaries); }
        catch (ImportException) { return null; }
        byte[]? block = Block(payload, 0x2C) ?? Block(payload, 0x1C);
        if (block is null && payload.Length >= Size * Size * 4) block = payload;
        if (block is null) return null;
        var linear = MateBytes(block);
        if (linear is null) return null;
        var layers = new int[Size * Size];
        int usable = 0;
        for (int i = 0; i < layers.Length; i++)
        {
            int a = linear[i * 4];
            int b = linear[i * 4 + 1];
            int blend = linear[i * 4 + 2];
            bool aOk = a is not 255 and not 127;
            bool bOk = b is not 255 and not 127;
            int layer = -1;
            if (aOk && bOk) layer = blend >= 128 && blend != 255 ? b : a;
            else if (aOk) layer = a;
            else if (bOk) layer = b;
            layers[i] = layer;
            if (layer >= 0) usable++;
        }
        return usable * 2 >= layers.Length ? layers : null;
    }

    static byte[]? MateBytes(byte[] block)
    {
        int tight = Size * Size * 4;
        if (block.Length == tight) return block;
        int swizzled = BlockLinear.SurfaceBytes(Size, Size, 1, 1, 4, HeightLog2);
        if (swizzled <= 0 || block.Length < swizzled) return null;
        var linear = BlockLinear.Deswizzle(Size, Size, 1, 1, 4, 0, HeightLog2, block.AsSpan(0, swizzled));
        if (linear.Length < tight) return null;
        return linear.AsSpan(0, tight).ToArray();
    }

    public static ImportedPart Build(
        IReadOnlyList<Tile> tiles, double scale, double heightScale,
        (double X, double Y, double Z) origin, (double X0, double Z0, double X1, double Z1)? clip,
        IReadOnlyDictionary<string, int>? steps = null)
    {
        var jobs = new List<Job>();
        foreach (var tile in tiles)
        {
            var box = WorldBox(tile, scale, origin);
            if (clip is { } window && !(box.X1 >= window.X0 && box.X0 <= window.X1 && box.Z1 >= window.Z0 && box.Z0 <= window.Z1))
                continue;
            int step = steps != null && steps.TryGetValue(tile.Name, out int chosen) ? chosen : 64;
            var samples = Indices(step);
            var grid = new float?[samples.Count][];
            for (int row = 0; row < samples.Count; row++)
            {
                grid[row] = new float?[samples.Count];
                for (int col = 0; col < samples.Count; col++)
                {
                    float value = tile.Heights[samples[row] * Size + samples[col]];
                    grid[row][col] = float.IsFinite(value) ? value : null;
                }
            }
            var cells = new List<(int Row, int Col, double X0, double Z0, double X1, double Z1)>();
            for (int row = 0; row < samples.Count - 1; row++)
            for (int col = 0; col < samples.Count - 1; col++)
            {
                if (grid[row][col] is null || grid[row][col + 1] is null || grid[row + 1][col] is null || grid[row + 1][col + 1] is null)
                    continue;
                double x0 = (tile.CenterX - tile.Edge / 2) + tile.Edge * samples[col] / (Size - 1);
                double x1 = (tile.CenterX - tile.Edge / 2) + tile.Edge * samples[col + 1] / (Size - 1);
                double z0 = (tile.CenterZ - tile.Edge / 2) + tile.Edge * samples[row] / (Size - 1);
                double z1 = (tile.CenterZ - tile.Edge / 2) + tile.Edge * samples[row + 1] / (Size - 1);
                cells.Add((row, col, x0, z0, x1, z1));
            }
            jobs.Add(new Job(tile, samples, grid, cells));
        }
        var meshes = new List<Cover>();
        foreach (var job in jobs.OrderBy(item => item.Tile.Edge).ThenBy(item => item.Tile.Name, StringComparer.Ordinal))
        {
            var kept = new HashSet<(int, int)>();
            foreach (var cell in job.Cells)
            {
                if (Covered((cell.X0 + cell.X1) * 0.5, (cell.Z0 + cell.Z1) * 0.5, job.Tile.Edge, meshes)) continue;
                kept.Add((cell.Row, cell.Col));
            }
            job.Keep = kept;
            meshes.Add(new Cover(
                job.Tile.Edge,
                job.Tile.CenterX - job.Tile.Edge / 2,
                job.Tile.CenterZ - job.Tile.Edge / 2,
                job.Tile.CenterX + job.Tile.Edge / 2,
                job.Tile.CenterZ + job.Tile.Edge / 2,
                job.Samples,
                kept));
        }
        var positions = new List<Vec3>();
        var indices = new List<int>();
        var uvs = new List<Vec2>();
        var layers = new List<int>();
        foreach (var job in jobs)
        {
            if (job.Keep.Count == 0) continue;
            double skirt = Math.Max(SkirtM, Math.Abs(job.Tile.Edge * scale) * 0.02);
            int last = job.Samples.Count - 2;
            var used = new Dictionary<(int, int), int>();
            int Vert(int row, int col, double down)
            {
                if (down == 0 && used.TryGetValue((row, col), out int existing)) return existing;
                int sx = job.Samples[col];
                int sz = job.Samples[row];
                double unitX = (job.Tile.CenterX - job.Tile.Edge / 2) + job.Tile.Edge * sx / (Size - 1);
                double unitZ = (job.Tile.CenterZ - job.Tile.Edge / 2) + job.Tile.Edge * sz / (Size - 1);
                float height = job.Grid[row][col] ?? 0;
                var position = new Vec3(unitX * scale + origin.X, height * heightScale + origin.Y - down, unitZ * scale + origin.Z);
                int index = positions.Count;
                positions.Add(position);
                uvs.Add(new Vec2(position.X / 8.0, position.Z / 8.0));
                int layer = -1;
                if (job.Tile.Layers is { Length: > 0 } grid)
                {
                    int sample = sz * Size + sx;
                    if ((uint)sample < (uint)grid.Length) layer = grid[sample];
                }
                layers.Add(layer);
                if (down == 0) used[(row, col)] = index;
                return index;
            }
            void FaceUp(int i0, int i1, int i2)
            {
                var p0 = positions[i0];
                var p1 = positions[i1];
                var p2 = positions[i2];
                double facing = (p1.Z - p0.Z) * (p2.X - p0.X) - (p1.X - p0.X) * (p2.Z - p0.Z);
                if (facing < 0) { indices.Add(i0); indices.Add(i2); indices.Add(i1); }
                else { indices.Add(i0); indices.Add(i1); indices.Add(i2); }
            }
            void SkirtEdge(int r0, int c0, int r1, int c1, string outward)
            {
                int top0 = Vert(r0, c0, 0);
                int top1 = Vert(r1, c1, 0);
                int bot0 = Vert(r0, c0, skirt);
                int bot1 = Vert(r1, c1, skirt);
                if (outward is "-z" or "+x")
                {
                    indices.Add(top0); indices.Add(top1); indices.Add(bot0);
                    indices.Add(top1); indices.Add(bot1); indices.Add(bot0);
                }
                else
                {
                    indices.Add(top0); indices.Add(bot0); indices.Add(top1);
                    indices.Add(bot0); indices.Add(bot1); indices.Add(top1);
                }
            }
            foreach (var (row, col) in job.Keep)
            {
                int a = Vert(row, col, 0);
                int b = Vert(row, col + 1, 0);
                int c = Vert(row + 1, col, 0);
                int d = Vert(row + 1, col + 1, 0);
                FaceUp(a, c, b);
                FaceUp(b, c, d);
                if (row == 0) SkirtEdge(row, col, row, col + 1, "-z");
                if (row == last) SkirtEdge(row + 1, col, row + 1, col + 1, "+z");
                if (col == 0) SkirtEdge(row, col, row + 1, col, "-x");
                if (col == last) SkirtEdge(row, col + 1, row + 1, col + 1, "+x");
            }
        }
        bool painted = layers.Any(layer => layer >= 0);
        return new ImportedPart
        {
            Name = "terrain",
            Positions = positions,
            Indices = indices,
            Uvs = uvs,
            Normals = MeshNormals.Generate(positions, indices),
            MaterialLayers = painted ? layers : null,
            Material = "terrain",
            Alpha = "opaque",
            DoubleSided = true,
        };
    }

    public static Dictionary<string, int> SampleSteps(
        IReadOnlyList<(double X, double Z, double Edge, string Name)> tiles,
        double scale, (double X, double Y, double Z) origin, (double X, double Z) focus)
    {
        int near = 0;
        var measured = new List<(string Name, double Distance, double Edge)>();
        foreach (var tile in tiles)
        {
            var box = WorldBox(tile.X, tile.Z, tile.Edge, scale, origin);
            double dx = (box.X0 + box.X1) / 2 - focus.X;
            double dz = (box.Z0 + box.Z1) / 2 - focus.Z;
            double distance = Math.Sqrt(dx * dx + dz * dz);
            measured.Add((tile.Name, distance, Math.Abs(tile.Edge * scale)));
            if (distance <= NearM) near++;
        }
        double nearSpacing = near > 24 ? 3 : 2;
        var steps = new Dictionary<string, int>();
        foreach (var (name, distance, edge) in measured)
        {
            double spacing = distance <= NearM ? nearSpacing : distance <= MidM ? 5 : 10;
            int step = StepFor(edge, spacing);
            if (edge <= 24 && step > 8) step = 8;
            else if (edge <= 80 && step > 16) step = 16;
            steps[name] = step;
        }
        return steps;
    }

    public static (double X0, double Z0, double X1, double Z1)? ActorClip(IEnumerable<(double X, double Z)> actors)
    {
        bool any = false;
        double minX = 0, minZ = 0, maxX = 0, maxZ = 0;
        foreach (var (x, z) in actors)
        {
            if (!any) { minX = maxX = x; minZ = maxZ = z; any = true; }
            else
            {
                minX = Math.Min(minX, x); maxX = Math.Max(maxX, x);
                minZ = Math.Min(minZ, z); maxZ = Math.Max(maxZ, z);
            }
        }
        if (!any) return null;
        return (minX - ClipPad, minZ - ClipPad, maxX + ClipPad, maxZ + ClipPad);
    }

    public static List<string> LevelStems(string banc)
    {
        string name = banc.Replace('\\', '/').Split('/').Last();
        foreach (string suffix in new[] { ".bcett.byml.zs", ".bcett.byml" })
        {
            if (name.EndsWith(suffix, StringComparison.Ordinal)) { name = name[..^suffix.Length]; break; }
        }
        var parts = banc.Replace('\\', '/').Split('/');
        string area = parts.Length >= 3 ? parts[1] : "";
        string basis = name;
        foreach (string suffix in new[] { "_Static", "_Dynamic" })
        {
            if (basis.EndsWith(suffix, StringComparison.Ordinal)) { basis = basis[..^suffix.Length]; break; }
        }
        var stems = new List<string>();
        foreach (string text in new[] { basis, area, name })
            if (text.Length > 0 && !stems.Contains(text)) stems.Add(text);
        return stems;
    }

    static int StepFor(double edgeMetres, double spacing)
    {
        if (edgeMetres <= 0) return Steps[^1];
        double raw = 256.0 * spacing / edgeMetres;
        foreach (int step in Steps)
            if (step + 1e-6 >= raw) return step;
        return Steps[^1];
    }

    static List<int> Indices(int step)
    {
        var found = new List<int>();
        for (int i = 0; i < Size; i += step) found.Add(i);
        if (found[^1] != Size - 1) found.Add(Size - 1);
        return found;
    }

    static bool Covered(double unitX, double unitZ, double edge, List<Cover> meshes)
    {
        foreach (var mesh in meshes)
        {
            if (mesh.Edge >= edge - 1e-4) continue;
            if (!(mesh.X0 < unitX && unitX < mesh.X1 && mesh.Z0 < unitZ && unitZ < mesh.Z1)) continue;
            double spanX = mesh.X1 - mesh.X0;
            double spanZ = mesh.Z1 - mesh.Z0;
            if (spanX <= 0 || spanZ <= 0) continue;
            int? column = Bracket(mesh.Samples, (unitX - mesh.X0) / spanX * (Size - 1));
            int? row = Bracket(mesh.Samples, (unitZ - mesh.Z0) / spanZ * (Size - 1));
            if (row is int r && column is int c && mesh.Kept.Contains((r, c))) return true;
        }
        return false;
    }

    static int? Bracket(List<int> samples, double unit)
    {
        if (samples.Count == 0 || unit < samples[0] || unit > samples[^1]) return null;
        for (int index = 0; index < samples.Count - 1; index++)
            if (samples[index] <= unit && unit <= samples[index + 1]) return index;
        return null;
    }

    static (double X0, double Z0, double X1, double Z1) WorldBox(Tile tile, double scale, (double X, double Y, double Z) origin) =>
        WorldBox(tile.CenterX, tile.CenterZ, tile.Edge, scale, origin);

    static (double X0, double Z0, double X1, double Z1) WorldBox(double cx, double cz, double edge, double scale, (double X, double Y, double Z) origin)
    {
        double half = edge / 2;
        return ((cx - half) * scale + origin.X, (cz - half) * scale + origin.Z, (cx + half) * scale + origin.X, (cz + half) * scale + origin.Z);
    }

    static byte[]? Block(byte[] data, int at)
    {
        if (at + 8 > data.Length) return null;
        int start;
        try { start = Bin.Rel32(data, at); }
        catch (ImportException) { return null; }
        int size = (int)Bin.U32(data, at + 4);
        if (size <= 0 || size > 32 * 1024 * 1024 || start < 0 || start + size > data.Length) return null;
        return data.AsSpan(start, size).ToArray();
    }

    static float[]? AsFloats(byte[] block)
    {
        int tight = Size * Size * 4;
        if (block.Length == tight)
        {
            var linear = new float[Size * Size];
            for (int i = 0; i < linear.Length; i++) linear[i] = Bin.F32(block, i * 4);
            return Uncover(FillZeros(linear));
        }
        var chosen = DecodeBlock(block, HeightLog2);
        if (chosen is null) return null;
        int zeros = chosen.Count(value => value == 0f);
        if (zeros > 64)
        {
            float rough = Roughness(chosen);
            foreach (int log2 in new[] { 0, 1, 2, 3, 5 })
            {
                var alt = DecodeBlock(block, log2);
                if (alt is null) continue;
                int altZeros = alt.Count(value => value == 0f);
                float altRough = Roughness(alt);
                if (altZeros * 2 < zeros && altRough <= rough)
                {
                    chosen = alt;
                    zeros = altZeros;
                    rough = altRough;
                }
            }
        }
        return Uncover(FillZeros(chosen));
    }

    static float[]? DecodeBlock(byte[] block, int log2)
    {
        int need = BlockLinear.SurfaceBytes(Size, Size, 1, 1, 4, log2);
        if (block.Length < need) return null;
        var linear = BlockLinear.Deswizzle(Size, Size, 1, 1, 4, 0, log2, block);
        if (linear.Length < Size * Size * 4) return null;
        var values = new float[Size * Size];
        for (int i = 0; i < values.Length; i++) values[i] = Bin.F32(linear, i * 4);
        int blockHeight = 1 << Math.Max(0, log2);
        int column = Size - 1;
        for (int y = 1; y < Size; y += 2)
        {
            int index = y * Size + column;
            if (values[index] != 0f) continue;
            int pos = BlockLinear.GobAddress(column, y - 1, Size, 4, blockHeight) + 4;
            if (pos < 0 || pos + 4 > block.Length) continue;
            float packed = Bin.F32(block, pos);
            if (float.IsFinite(packed) && packed != 0f) values[index] = packed;
        }
        return values;
    }

    static float[] FillZeros(float[] values)
    {
        var current = (float[])values.Clone();
        for (int pass = 0; pass < 2; pass++)
        {
            var next = (float[])current.Clone();
            for (int z = 0; z < Size; z++)
            for (int x = 0; x < Size; x++)
            {
                if (next[z * Size + x] != 0f) continue;
                float left = x > 0 ? next[z * Size + x - 1] : 0;
                float right = x + 1 < Size ? next[z * Size + x + 1] : 0;
                if (left != 0 && right != 0 && float.IsFinite(left) && float.IsFinite(right))
                    next[z * Size + x] = (left + right) * 0.5f;
                else if (x == Size - 1 && left != 0 && float.IsFinite(left)) next[z * Size + x] = left;
                else if (x == 0 && right != 0 && float.IsFinite(right)) next[z * Size + x] = right;
            }
            current = next;
        }
        return current;
    }

    static float[] Uncover(float[] values)
    {
        var output = new float[values.Length];
        for (int i = 0; i < values.Length; i++)
            output[i] = values[i] == 0f ? float.NaN : values[i];
        return output;
    }

    static float Roughness(float[] values)
    {
        double total = 0;
        int count = 0;
        for (int z = 0; z < Size - 1; z++)
        for (int x = 0; x < Size - 1; x++)
        {
            float here = values[z * Size + x];
            if (!float.IsFinite(here)) continue;
            float right = values[z * Size + x + 1];
            float down = values[(z + 1) * Size + x];
            if (float.IsFinite(right)) { total += Math.Abs(here - right); count++; }
            if (float.IsFinite(down)) { total += Math.Abs(here - down); count++; }
        }
        return count == 0 ? 1e9f : (float)(total / count);
    }

    sealed class Job
    {
        public Tile Tile { get; }
        public List<int> Samples { get; }
        public float?[][] Grid { get; }
        public List<(int Row, int Col, double X0, double Z0, double X1, double Z1)> Cells { get; }
        public HashSet<(int, int)> Keep { get; set; } = new();
        public Job(Tile tile, List<int> samples, float?[][] grid, List<(int, int, double, double, double, double)> cells)
        {
            Tile = tile;
            Samples = samples;
            Grid = grid;
            Cells = cells;
        }
    }

    sealed record Cover(double Edge, double X0, double Z0, double X1, double Z1, List<int> Samples, HashSet<(int, int)> Kept);
}
