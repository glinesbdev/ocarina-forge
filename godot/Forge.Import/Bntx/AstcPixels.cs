using AssetRipper.TextureDecoder.Astc;
using AssetRipper.TextureDecoder.Rgb.Formats;

namespace OcarinaForge.Import;

public static class AstcPixels
{
    /// <summary>Decodes linear ASTC mip zero to tightly packed RGBA8 pixels.</summary>
    public static byte[] DecodeMipZero(CompressedTexture image)
    {
        int mipBytes = MipChain.LevelBytes(image.Width, image.Height, image.BlockW, image.BlockH, image.BytesPerBlock, false);
        var pixels = new byte[checked(image.Width * image.Height * 4)];
        AstcDecoder.DecodeASTC<ColorRGBA<byte>, byte>(image.Blocks.AsSpan(0, mipBytes),
            image.Width, image.Height, image.BlockW, image.BlockH, pixels);
        return pixels;
    }
}
