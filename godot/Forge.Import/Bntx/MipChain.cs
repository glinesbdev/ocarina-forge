namespace OcarinaForge.Import;

/// <summary>
/// Byte size of a mip chain down to 1×1. Godot's <c>Image.CreateFromData</c> uses this
/// size when mipmaps are enabled: <c>floor(log2(max(width, height)))</c> extra levels,
/// and every compressed level is at least one block even when the dimension is 1 or 2.
/// </summary>
public static class MipChain
{
    public static int LevelBytes(int width, int height, int blockW, int blockH, int bytesPerBlock, bool pixels)
    {
        int w = Math.Max(1, width);
        int h = Math.Max(1, height);
        if (pixels) return w * h * 4;
        return BlockLinear.DivUp(w, Math.Max(1, blockW)) * BlockLinear.DivUp(h, Math.Max(1, blockH)) * Math.Max(1, bytesPerBlock);
    }

    public static int Expected(int width, int height, int blockW, int blockH, int bytesPerBlock, bool pixels)
    {
        int total = 0;
        int w = Math.Max(1, width);
        int h = Math.Max(1, height);
        while (true)
        {
            total += LevelBytes(w, h, blockW, blockH, bytesPerBlock, pixels);
            if (w == 1 && h == 1) break;
            w = Math.Max(1, w / 2);
            h = Math.Max(1, h / 2);
        }
        return total;
    }

    /// <summary>
    /// Pad a chain that stops early (copy the last block into the missing tail) or trim a long one.
    /// A buffer that is already the right length is returned as-is.
    /// </summary>
    public static byte[] Fit(int width, int height, int blockW, int blockH, int bytesPerBlock, bool pixels, byte[] blocks)
    {
        int expected = Expected(width, height, blockW, blockH, bytesPerBlock, pixels);
        if (blocks.Length == expected) return blocks;
        var output = new byte[expected];
        blocks.AsSpan(0, Math.Min(blocks.Length, expected)).CopyTo(output);
        int block = pixels ? 4 : Math.Max(1, bytesPerBlock);
        if (blocks.Length < expected && blocks.Length >= block)
        {
            var last = blocks.AsSpan(blocks.Length - block, block);
            for (int at = blocks.Length; at + block <= expected; at += block)
                last.CopyTo(output.AsSpan(at));
        }
        return output;
    }
}
