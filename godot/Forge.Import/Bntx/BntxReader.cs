namespace OcarinaForge.Import;

public enum GpuCodec
{
    Unknown,
    Rgba8,
    Bc1,
    Bc2,
    Bc3,
    Bc4,
    Bc5,
    Bc6,
    Bc7,
    Astc,
}

public sealed class CompressedTexture
{
    public string Name { get; init; } = "";
    public int Width { get; init; }
    public int Height { get; init; }
    public int FormatId { get; init; }
    public GpuCodec Codec { get; init; }
    public bool Srgb { get; init; }
    public int BlockW { get; init; }
    public int BlockH { get; init; }
    public int BytesPerBlock { get; init; }
    /// <summary>Linear compressed blocks, mip 0 first. Not decoded to RGBA.</summary>
    public byte[] Blocks { get; init; } = Array.Empty<byte>();
    public int MipCount { get; init; } = 1;
    public string Note { get; init; } = "";
}

/// <summary>
/// Reads BNTX headers and deswizzles compressed blocks. It does not decode BC7 or ASTC to RGBA.
/// </summary>
public static class BntxReader
{
    static readonly Dictionary<int, (GpuCodec Codec, int W, int H, int Bytes, string Name)> Formats = new()
    {
        [0x0B01] = (GpuCodec.Rgba8, 1, 1, 4, "rgba8"),
        [0x0B02] = (GpuCodec.Rgba8, 1, 1, 4, "rgba8"),
        [0x0B05] = (GpuCodec.Rgba8, 1, 1, 4, "rgba8"),
        [0x0B06] = (GpuCodec.Rgba8, 1, 1, 4, "rgba8"),
        [0x1A01] = (GpuCodec.Bc1, 4, 4, 8, "bc1"),
        [0x1A06] = (GpuCodec.Bc1, 4, 4, 8, "bc1"),
        [0x1B01] = (GpuCodec.Bc2, 4, 4, 16, "bc2"),
        [0x1B06] = (GpuCodec.Bc2, 4, 4, 16, "bc2"),
        [0x1C01] = (GpuCodec.Bc3, 4, 4, 16, "bc3"),
        [0x1C06] = (GpuCodec.Bc3, 4, 4, 16, "bc3"),
        [0x1D01] = (GpuCodec.Bc4, 4, 4, 8, "bc4"),
        [0x1E01] = (GpuCodec.Bc5, 4, 4, 16, "bc5"),
        [0x1F05] = (GpuCodec.Bc6, 4, 4, 16, "bc6"),
        [0x1F0A] = (GpuCodec.Bc6, 4, 4, 16, "bc6"),
        [0x2001] = (GpuCodec.Bc7, 4, 4, 16, "bc7"),
        [0x2006] = (GpuCodec.Bc7, 4, 4, 16, "bc7"),
        [0x2D01] = (GpuCodec.Astc, 4, 4, 16, "astc4"),
        [0x2D06] = (GpuCodec.Astc, 4, 4, 16, "astc4"),
        [0x3401] = (GpuCodec.Astc, 8, 8, 16, "astc8"),
        [0x3406] = (GpuCodec.Astc, 8, 8, 16, "astc8"),
    };

    public static bool LooksLike(ReadOnlySpan<byte> data) =>
        data.Length >= 4 && data[0] == (byte)'B' && data[1] == (byte)'N' && data[2] == (byte)'T' && data[3] == (byte)'X';

    public static IReadOnlyList<CompressedTexture> Read(byte[] data)
    {
        if (data.Length < 0x40 || !LooksLike(data))
            throw new ImportException("not a BNTX texture container");
        if (data[0x0C] != 0xFF || data[0x0D] != 0xFE)
            throw new ImportException("only little-endian BNTX is supported");
        int count = Bin.I32(data, 0x24);
        int table = (int)Bin.U64(data, 0x28);
        if (count <= 0 || count > 4096 || table <= 0) return Array.Empty<CompressedTexture>();
        var found = new List<CompressedTexture>();
        for (int index = 0; index < count; index++)
        {
            int at = table + index * 8;
            if (at + 8 > data.Length) break;
            int brti = (int)Bin.U64(data, at);
            if (brti == 0) continue;
            found.Add(ReadTexture(data, brti));
        }
        return found;
    }

    public static CompressedTexture ReadTexture(byte[] data, int at)
    {
        if (at + 0x80 > data.Length || data[at] != (byte)'B' || data[at + 1] != (byte)'R')
            throw new ImportException($"texture header at 0x{at:X} is not BRTI");
        int tile = Bin.U16(data, at + 0x12);
        int format = (int)Bin.U32(data, at + 0x1C);
        int width = Bin.I32(data, at + 0x24);
        int height = Bin.I32(data, at + 0x28);
        string name = Bin.LengthString(data, (int)Bin.U64(data, at + 0x60));
        if (width <= 0 || height <= 0 || width > 16384 || height > 16384)
            throw new ImportException($"{name}: size {width}x{height} is not usable");
        if (!TryFormat(format, out var info))
            throw new ImportException($"{name}: texture format 0x{format:X4} is not kept compressed");
        int arrayCount = at + 0x34 <= data.Length ? (int)Bin.U32(data, at + 0x30) : 1;
        if (arrayCount <= 0 || arrayCount > 4096) arrayCount = 1;
        var offsets = MipOffsets(data, at);
        if (offsets.Count == 0)
            throw new ImportException($"{name}: image data is missing");
        int mip0 = offsets.Count >= 2 ? offsets[1] - offsets[0] : data.Length - offsets[0];
        if (arrayCount > 1)
        {
            int imageSize = (int)Bin.U32(data, at + 0x50);
            if (imageSize <= 0 || imageSize % arrayCount != 0)
                throw new ImportException($"{name}: array of {arrayCount} has no per-layer size");
            int stride = imageSize / arrayCount;
            if (mip0 <= 0 || mip0 > stride) mip0 = stride;
        }
        var layout = data.AsSpan(at + 0x34, Math.Min(8, data.Length - (at + 0x34)));
        int log2 = BlockLinear.StoredLog2(layout);
        if (log2 < 0) log2 = 0;
        bool srgb = (format & 0xFF) == 0x06;
        var mips = new List<byte>();
        int mipW = width;
        int mipH = height;
        int count = Math.Max(1, offsets.Count);
        for (int mip = 0; mip < count; mip++)
        {
            int start = offsets[Math.Min(mip, offsets.Count - 1)];
            int size = mip + 1 < offsets.Count ? offsets[mip + 1] - start : (mip == 0 ? mip0 : 0);
            if (mip > 0 && size <= 0) break;
            if (start < 0 || size <= 0 || start + size > data.Length) break;
            var linear = BlockLinear.Deswizzle(mipW, mipH, info.W, info.H, info.Bytes, tile, log2, data.AsSpan(start, size));
            mips.AddRange(linear);
            if (mipW <= info.W && mipH <= info.H) break;
            mipW = Math.Max(1, mipW / 2);
            mipH = Math.Max(1, mipH / 2);
            log2 = 0;
        }
        return new CompressedTexture
        {
            Name = name,
            Width = width,
            Height = height,
            FormatId = format,
            Codec = info.Codec,
            Srgb = srgb && info.Codec is GpuCodec.Bc1 or GpuCodec.Bc7 or GpuCodec.Bc3 or GpuCodec.Rgba8 or GpuCodec.Astc,
            BlockW = info.W,
            BlockH = info.H,
            BytesPerBlock = info.Bytes,
            Blocks = mips.ToArray(),
            MipCount = count,
            Note = $"{info.Name} {(srgb ? "srgb" : "linear")} 0x{format:X4}, {mips.Count} compressed bytes",
        };
    }

    /// <summary>A linear (tile mode 1) BNTX the tests and the synthetic dump can write. Not a game file.</summary>
    public static byte[] WriteLinear(string name, int width, int height, int format, byte[] tightBlocks)
    {
        if (!TryFormat(format, out var info))
            throw new ImportException($"cannot write format 0x{format:X4}");
        int blocksX = BlockLinear.DivUp(width, info.W);
        int blocksY = BlockLinear.DivUp(height, info.H);
        int tight = blocksX * blocksY * info.Bytes;
        if (tightBlocks.Length < tight)
            throw new ImportException("compressed block buffer is short");
        var blob = new List<byte>(0x200 + tight);
        void Pad(int n) { while (blob.Count % n != 0) blob.Add(0); }
        blob.AddRange(new byte[0x40]);
        blob[0] = (byte)'B'; blob[1] = (byte)'N'; blob[2] = (byte)'T'; blob[3] = (byte)'X';
        blob[0x0C] = 0xFF; blob[0x0D] = 0xFE;
        int nameAt = blob.Count;
        var raw = System.Text.Encoding.ASCII.GetBytes(name);
        blob.Add((byte)raw.Length); blob.Add((byte)(raw.Length >> 8));
        blob.AddRange(raw); blob.Add(0);
        Pad(8);
        int imageAt = blob.Count;
        blob.AddRange(tightBlocks.AsSpan(0, tight));
        Pad(8);
        int mipAt = blob.Count;
        blob.AddRange(U64((ulong)imageAt));
        Pad(8);
        int brti = blob.Count;
        var header = new byte[0x80];
        header[0] = (byte)'B'; header[1] = (byte)'R'; header[2] = (byte)'T'; header[3] = (byte)'I';
        System.Buffers.Binary.BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(0x12), 1);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(0x1C), (uint)format);
        System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(header.AsSpan(0x24), width);
        System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(header.AsSpan(0x28), height);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(0x30), 1);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt64LittleEndian(header.AsSpan(0x60), (ulong)nameAt);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt64LittleEndian(header.AsSpan(0x70), (ulong)mipAt);
        blob.AddRange(header);
        Pad(8);
        int table = blob.Count;
        blob.AddRange(U64((ulong)brti));
        WriteAt(blob, 0x24, U32(1));
        WriteAt(blob, 0x28, U64((ulong)table));
        return blob.ToArray();
    }

    static List<int> MipOffsets(byte[] data, int at)
    {
        var offsets = new List<int>();
        if (at + 0x78 > data.Length) return offsets;
        int table = (int)Bin.U64(data, at + 0x70);
        if (table == 0) return offsets;
        for (int index = 0; index < 17; index++)
        {
            int slot = table + index * 8;
            if (slot + 8 > data.Length) break;
            int value = (int)Bin.U64(data, slot);
            if (value <= 0 || value > data.Length) break;
            if (offsets.Count > 0 && value <= offsets[^1]) break;
            offsets.Add(value);
        }
        return offsets;
    }

    public static bool TryFormat(int format, out (GpuCodec Codec, int W, int H, int Bytes, string Name) info)
    {
        if (Formats.TryGetValue(format, out info)) return true;
        int family = format & 0xFF00;
        foreach (var (known, value) in Formats)
        {
            if ((known & 0xFF00) == family)
            {
                info = value;
                return true;
            }
        }
        info = default;
        return false;
    }

    static void WriteAt(List<byte> blob, int at, byte[] word)
    {
        for (int i = 0; i < word.Length; i++) blob[at + i] = word[i];
    }

    static byte[] U32(uint value)
    {
        var word = new byte[4];
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(word, value);
        return word;
    }
    static byte[] U64(ulong value)
    {
        var word = new byte[8];
        System.Buffers.Binary.BinaryPrimitives.WriteUInt64LittleEndian(word, value);
        return word;
    }
}
