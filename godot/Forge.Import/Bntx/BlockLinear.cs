namespace OcarinaForge.Import;

/// <summary>
/// Tegra X1 block-linear addresses, plus the Switch 2 GOB order for 16-byte elements.
/// 16-byte blocks (BC7, BC3, BC5, ASTC) use Switch 2: bit 5 is y1 and bit 6 is x0.
/// BC1, BC4, and 4-byte height samples stay on Tegra X1. This is a port of
/// <c>ocarina_forge/formats/swizzle.py</c>, which is an original implementation of the TRM layout.
/// </summary>
public static class BlockLinear
{
    public const string Tegra = "tegra";
    public const string Switch2 = "switch2";

    public static int DivUp(int value, int block) => block == 0 ? 0 : (value + block - 1) / block;
    public static int RoundUp(int value, int alignment) => alignment <= 1 ? value : ((value - 1) | (alignment - 1)) + 1;

    public static string OrderFor(int bytesPer) => bytesPer >= 16 ? Switch2 : Tegra;

    public static int SurfaceBytes(int width, int height, int blockW, int blockH, int bytesPer, int log2)
    {
        int blocksX = Math.Max(1, DivUp(width, Math.Max(blockW, 1)));
        int blocksY = Math.Max(1, DivUp(height, Math.Max(blockH, 1)));
        int pitch = RoundUp(blocksX * bytesPer, 64);
        int rows = RoundUp(blocksY, (1 << Math.Clamp(log2, 0, 5)) * 8);
        return pitch * rows;
    }

    public static int StoredLog2(ReadOnlySpan<byte> layout)
    {
        if (layout.IsEmpty) return -1;
        int legacy = layout.Length >= 5 ? layout[4] : 0xFF;
        if (layout[0] == 0 && legacy <= 5) return legacy;
        int low = layout[0] & 7;
        if (low <= 5) return low;
        if (legacy <= 5) return legacy;
        return -1;
    }

    public static byte[] Deswizzle(int width, int height, int blockW, int blockH, int bytesPer, int tileMode, int log2, ReadOnlySpan<byte> swizzled, string? order = null)
    {
        return Move(width, height, blockW, blockH, bytesPer, tileMode, log2, swizzled, toSwizzle: false, order);
    }

    public static byte[] Swizzle(int width, int height, int blockW, int blockH, int bytesPer, int tileMode, int log2, ReadOnlySpan<byte> linear, string? order = null)
    {
        return Move(width, height, blockW, blockH, bytesPer, tileMode, log2, linear, toSwizzle: true, order);
    }

    static int GobLocal(int xx, int y, string order)
    {
        if (order == Switch2)
        {
            return ((xx % 64) / 32) * 256
                + ((y % 8) / 4) * 128
                + ((xx % 32) / 16) * 64
                + ((y % 4) / 2) * 32
                + (y % 2) * 16
                + (xx % 16);
        }
        return ((xx % 64) / 32) * 256
            + ((y % 8) / 2) * 64
            + ((xx % 32) / 16) * 32
            + (y % 2) * 16
            + (xx % 16);
    }

    public static int GobAddress(int x, int y, int widthBlocks, int bytesPer, int blockHeight, string? order = null)
    {
        string chosen = order ?? OrderFor(bytesPer);
        int imageWidthInGobs = DivUp(widthBlocks * bytesPer, 64);
        int gob = (y / (8 * blockHeight)) * 512 * blockHeight * imageWidthInGobs
            + (x * bytesPer / 64) * 512 * blockHeight
            + ((y % (8 * blockHeight)) / 8) * 512;
        return gob + GobLocal(x * bytesPer, y, chosen);
    }

    static byte[] Move(int width, int height, int blockW, int blockH, int bytesPer, int tileMode, int log2, ReadOnlySpan<byte> data, bool toSwizzle, string? order)
    {
        int blocksX = Math.Max(1, DivUp(width, blockW));
        int blocksY = Math.Max(1, DivUp(height, blockH));
        int tight = blocksX * blocksY * bytesPer;
        if (tileMode == 1)
        {
            var linear = new byte[tight];
            data.Slice(0, Math.Min(tight, data.Length)).CopyTo(linear);
            return linear;
        }
        int blockHeight = 1 << Math.Max(0, log2);
        byte[] output;
        if (toSwizzle)
        {
            int pitch = RoundUp(blocksX * bytesPer, 64);
            int surface = pitch * RoundUp(blocksY, blockHeight * 8);
            output = new byte[surface];
        }
        else output = new byte[tight];
        string chosen = order ?? OrderFor(bytesPer);
        for (int y = 0; y < blocksY; y++)
        {
            for (int x = 0; x < blocksX; x++)
            {
                int pos = GobAddress(x, y, blocksX, bytesPer, blockHeight, chosen);
                int linear = (y * blocksX + x) * bytesPer;
                if (toSwizzle)
                {
                    if (pos + bytesPer <= output.Length && linear + bytesPer <= data.Length)
                        data.Slice(linear, bytesPer).CopyTo(output.AsSpan(pos));
                }
                else if (pos + bytesPer <= data.Length && linear + bytesPer <= output.Length)
                {
                    data.Slice(pos, bytesPer).CopyTo(output.AsSpan(linear));
                }
            }
        }
        return output;
    }
}
