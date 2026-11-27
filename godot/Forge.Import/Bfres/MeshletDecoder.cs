namespace OcarinaForge.Import;

public sealed class MeshletException : ImportException
{
    public MeshletException(string message) : base(message) { }
}

public sealed class MeshletLevel
{
    public int Number { get; init; }
    public List<(double X, double Y, double Z)> Positions { get; } = new();
    public List<(double X, double Y, double Z)> Normals { get; } = new();
    public List<int> Indices { get; } = new();
    public List<List<(double U, double V)>> UvSets { get; } = new();
    public List<(double R, double G, double B, double A)>? Colors { get; init; }
}

/// <summary>
/// BFRES 10.4 packed meshlets. The bit layout is the one documented by NUP Explorer
/// and implemented in <c>ocarina_forge/formats/meshlet.py</c>. This is a port of that
/// file, not a copy of NupExplorer.
/// </summary>
public static class MeshletDecoder
{
    const int Records = 0x54;
    static readonly int[] XyzShifts = { 0, 21, 42 };
    const int QuantizedMask = (1 << 21) - 1;

    sealed class Record
    {
        public ulong Corner;
        public int[] Widths = Array.Empty<int>();
        public int NormalWidth;
        public int AttributeStart;
        public List<(uint First, uint Second)> Attributes = new();
        public int Triangles;
        public int Vertices;
        public int Offset;
        public int Stride;
        public uint RawOffset;
    }

    public static List<MeshletLevel> Decode(byte[] data, int shape, int gpuBase)
    {
        var (start, size) = BufferSpan(data, shape, gpuBase);
        int descriptor = (int)Bin.U64(data, shape + 0x18);
        int levels = data[descriptor + 0x45];
        if (levels is < 1 or > 16 || size < Records + 32)
            throw new MeshletException("invalid meshlet LOD table");
        var block = data.AsSpan(start, size);
        double ox = Bin.F32(block, 0), oy = Bin.F32(block, 4), oz = Bin.F32(block, 8), step = Bin.F32(block, 12);
        if (!double.IsFinite(ox) || !double.IsFinite(oy) || !double.IsFinite(oz) || !double.IsFinite(step) || step <= 0)
            throw new MeshletException("invalid meshlet quantization");
        var limits = new int[levels];
        for (int i = 0; i < levels; i++) limits[i] = (int)Bin.U32(block, 0x14 + i * 4);
        if (limits[0] == 0) throw new MeshletException("invalid cumulative meshlet counts");
        for (int i = 0; i < levels - 1; i++)
            if (limits[i] > limits[i + 1]) throw new MeshletException("invalid cumulative meshlet counts");
        int firstPayload = (int)(Bin.U32(block, Records + 0x14) >> 4);
        int stride = Math.DivRem(firstPayload, limits[^1], out int remainder);
        int attributeCount = Math.DivRem(stride - 32, 8, out int extra);
        if (remainder != 0 || attributeCount < 0 || extra != 0 || stride > 160 || Records + firstPayload > size)
            throw new MeshletException("invalid meshlet record table");
        int? far = null;
        for (int level = levels - 1; level > 0; level--)
        {
            int count = limits[level] - limits[level - 1];
            if (count > 0 && count < limits[0]) { far = level; break; }
        }
        var records = new List<Record>();
        for (int index = 0; index < limits[^1]; index++)
        {
            int record = Records + index * stride;
            ulong corner = Bin.U64(block, record);
            ulong extent = Bin.U64(block, record + 8);
            int triangles = block[record + 18];
            int vertices = block[record + 19];
            uint rawOffset = Bin.U32(block, record + 20);
            uint bits = Bin.U32(block, record + 24);
            uint tangent = Bin.U32(block, record + 28);
            var widths = new int[3];
            for (int axis = 0; axis < 3; axis++)
                widths[axis] = BitLength((int)((extent >> XyzShifts[axis]) & QuantizedMask));
            int positionBits = widths.Sum();
            int normalWidth = 6 + (int)(rawOffset & 15);
            int attributeStart = positionBits + 2 * normalWidth + (tangent == 2 ? 17 : 0);
            var attributes = new List<(uint, uint)>();
            for (int at = record + 32; at + 8 <= record + stride; at += 8)
                attributes.Add((Bin.U32(block, at), Bin.U32(block, at + 4)));
            records.Add(new Record
            {
                Corner = corner,
                Widths = widths,
                NormalWidth = normalWidth,
                AttributeStart = attributeStart,
                Attributes = attributes,
                Triangles = triangles,
                Vertices = vertices,
                Offset = (int)(rawOffset >> 4),
                Stride = (int)(bits & 0x1FF),
                RawOffset = rawOffset,
            });
        }
        int uvCount = TextureSetCount(records, attributeCount);
        var result = new List<MeshletLevel>();
        foreach (int number in far is null ? new[] { 0 } : new[] { 0, far.Value })
        {
            var level = new MeshletLevel
            {
                Number = number,
                Colors = uvCount < attributeCount ? new List<(double, double, double, double)>() : null,
            };
            for (int i = 0; i < uvCount; i++) level.UvSets.Add(new List<(double, double)>());
            int low = number == 0 ? 0 : limits[number - 1];
            int high = limits[number];
            for (int index = low; index < high; index++)
            {
                var record = records[index];
                if (record.Vertices is < 3 or > 64 || record.Triangles is < 1 or > 124 || record.AttributeStart > record.Stride)
                    throw new MeshletException("invalid meshlet vertex or triangle record");
                int indexWidth = BitLength(record.Vertices - 1);
                int byteSize = (record.Vertices * record.Stride + 3 * record.Triangles * indexWidth + 7) / 8;
                int payloadAt = Records + record.Offset;
                if (record.Offset < firstPayload || payloadAt + byteSize > size)
                    throw new MeshletException("meshlet bitstream is outside the packed buffer");
                var payload = block.Slice(payloadAt, byteSize);
                int baseVertex = level.Positions.Count;
                for (int corner = 0; corner < 3 * record.Triangles; corner++)
                {
                    int vertex = (int)ReadBits(payload, record.Vertices * record.Stride + corner * indexWidth, indexWidth);
                    if (vertex >= record.Vertices)
                        throw new MeshletException("meshlet triangle index is outside its vertex list");
                    level.Indices.Add(baseVertex + vertex);
                }
                DecodeVertices(level, payload, record, ox, oy, oz, step);
            }
            result.Add(level);
        }
        return result;
    }

    static (int Start, int Size) BufferSpan(byte[] data, int shape, int gpuBase)
    {
        if (data.Length < 0x0C || Bin.U32(data, 0x08) != 0x000A0400)
            throw new MeshletException("meshlet decoding supports BFRES 10.4.0");
        if (shape < 0 || shape + 0x20 > data.Length || data[shape] != (byte)'F' || data[shape + 1] != (byte)'S')
            throw new MeshletException("meshlet shape header is outside the file or has no FSHP signature");
        if ((Bin.U32(data, shape + 4) & 0x10) == 0)
            throw new MeshletException("shape does not declare meshlet data");
        int descriptor = (int)Bin.U64(data, shape + 0x18);
        if (descriptor == 0 || descriptor + 0x48 > data.Length)
            throw new MeshletException("meshlet descriptor is missing or outside the file");
        int count = data[descriptor + 0x44];
        if (count != 1)
            throw new MeshletException($"meshlet descriptor has {count} buffers; expected one");
        int info = (int)Bin.U64(data, descriptor + 0x10);
        int offsets = (int)Bin.U64(data, descriptor + 0x18);
        if (info == 0 || info + 4 > data.Length || offsets == 0 || offsets + 8 > data.Length)
            throw new MeshletException("meshlet buffer tables are missing or outside the file");
        int size = (int)Bin.U32(data, info);
        long relative = Bin.I64(data, offsets);
        long start = gpuBase + relative;
        if (gpuBase <= 0 || relative < 0 || size == 0 || start + size > data.Length)
            throw new MeshletException("packed meshlet buffer is empty or outside the GPU data");
        return ((int)start, size);
    }

    static void DecodeVertices(MeshletLevel level, ReadOnlySpan<byte> payload, Record record, double ox, double oy, double oz, double scale)
    {
        var origin = new[] { ox, oy, oz };
        var cluster = new int[3];
        for (int axis = 0; axis < 3; axis++)
            cluster[axis] = (int)((record.Corner >> XyzShifts[axis]) & (ulong)QuantizedMask);
        int uvCount = level.UvSets.Count;
        var sets = new List<(int Offset, short U, short V, int Eu, int Ev, int Wu, int Wv)>();
        var colorDescriptors = record.Attributes.Skip(uvCount).ToList();
        int cursor = record.AttributeStart + colorDescriptors.Sum(item => ColorBits(item.First));
        foreach (var (packed, flags) in record.Attributes.Take(uvCount))
        {
            short u = (short)(packed & 0xFFFF);
            short v = (short)(packed >> 16);
            int eu = (int)(flags & 15) - 16;
            int ev = (int)((flags >> 12) & 15) - 16;
            var (wu, wv) = UvWidths(flags);
            sets.Add((cursor, u, v, eu, ev, wu, wv));
            cursor += wu + wv;
        }
        uint colorFlags = 0, colorConstants = 0;
        int colorWidth = 0;
        if (level.Colors is not null && colorDescriptors.Count > 0)
        {
            colorFlags = colorDescriptors[0].First;
            colorConstants = colorDescriptors[0].Second;
            colorWidth = ColorWidth(colorFlags);
        }
        double normalScale = 2.0 / ((1 << record.NormalWidth) - 1);
        for (int vertex = 0; vertex < record.Vertices; vertex++)
        {
            int bit = vertex * record.Stride;
            int local = 0;
            var point = new double[3];
            for (int axis = 0; axis < 3; axis++)
            {
                point[axis] = origin[axis] + scale * (cluster[axis] + (int)ReadBits(payload, bit + local, record.Widths[axis]));
                local += record.Widths[axis];
            }
            level.Positions.Add((point[0], point[1], point[2]));
            double x = ReadBits(payload, bit + local, record.NormalWidth) * normalScale - 1;
            double y = ReadBits(payload, bit + local + record.NormalWidth, record.NormalWidth) * normalScale - 1;
            double z = 1 - Math.Abs(x) - Math.Abs(y);
            if (z < 0)
            {
                double originalX = x;
                x = Math.CopySign(1 - Math.Abs(y), x);
                y = Math.CopySign(1 - Math.Abs(originalX), y);
            }
            double length = Math.Sqrt(x * x + y * y + z * z);
            level.Normals.Add((x / length, y / length, z / length));
            for (int channel = 0; channel < sets.Count; channel++)
            {
                var set = sets[channel];
                double u = Math.ScaleB(set.U + (double)ReadBits(payload, bit + set.Offset, set.Wu), set.Eu);
                double v = Math.ScaleB(set.V + (double)ReadBits(payload, bit + set.Offset + set.Wu, set.Wv), set.Ev);
                level.UvSets[channel].Add((u, v));
            }
            if (level.Colors is not null)
            {
                var channels = new double[4];
                int colorCursor = record.AttributeStart;
                for (int channel = 0; channel < 4; channel++)
                {
                    double value;
                    if ((colorFlags & (1u << channel)) != 0)
                        value = ((colorConstants >> (channel * 8)) & 0xFF) / 255.0;
                    else
                    {
                        ulong raw = ReadBits(payload, bit + colorCursor, colorWidth);
                        value = colorWidth == 8 ? raw / 255.0 : BitConverter.UInt32BitsToSingle((uint)raw);
                        colorCursor += colorWidth;
                    }
                    channels[channel] = Math.Clamp(value, 0, 1);
                }
                level.Colors.Add((channels[0], channels[1], channels[2], channels[3]));
            }
        }
    }

    static int TextureSetCount(List<Record> records, int attributes)
    {
        var candidates = new HashSet<int>(Enumerable.Range(0, attributes + 1));
        int uvHint = 0;
        foreach (var (_, flags) in records[0].Attributes)
        {
            if ((flags & 0x00FF0FF0) != 0 || (flags & 15) == 0 || ((flags >> 12) & 15) == 0) break;
            uvHint++;
        }
        foreach (var record in records)
        {
            foreach (int count in candidates.ToArray())
            {
                int uvBits = record.Attributes.Take(count).Sum(item => { var (u, v) = UvWidths(item.Second); return u + v; });
                int colorBits = record.Attributes.Skip(count).Sum(item => ColorBits(item.First));
                if (record.AttributeStart + uvBits + colorBits != record.Stride)
                    candidates.Remove(count);
            }
            if (candidates.Count == 0)
                throw new MeshletException("meshlet attributes disagree with the vertex bit stride");
        }
        return candidates.MinBy(count => (Math.Abs(count - uvHint), count));
    }

    static int ColorWidth(uint flags) => (flags & 0xF0) == 0xF0 ? 8 : 32;
    static int ColorBits(uint flags) => (4 - BitOperationsCount(flags & 15)) * ColorWidth(flags);
    static (int U, int V) UvWidths(uint flags) => ((int)((flags >> 24) & 15) + 1, (int)((flags >> 28) & 15) + 1);

    static int BitOperationsCount(uint value)
    {
        int count = 0;
        while (value != 0) { count += (int)(value & 1); value >>= 1; }
        return count;
    }

    static int BitLength(int value)
    {
        int bits = 0;
        while (value > 0) { bits++; value >>= 1; }
        return bits;
    }

    static ulong ReadBits(ReadOnlySpan<byte> data, int bitOffset, int width)
    {
        if (width <= 0) return 0;
        if (width > 32) throw new MeshletException("bit field wider than 32");
        int byteIndex = bitOffset >> 3;
        int shift = bitOffset & 7;
        ulong value = 0;
        int have = 0;
        while (have < width + shift && byteIndex < data.Length)
        {
            value |= (ulong)data[byteIndex] << have;
            have += 8;
            byteIndex++;
        }
        return (value >> shift) & ((1ul << width) - 1);
    }

}
