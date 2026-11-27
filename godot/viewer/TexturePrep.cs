using Godot;
using OcarinaForge.Import;

namespace OcarinaForge.Viewer;

/// <summary>
/// Turns ASTC into BC7 before the cache is written. AMD desktop Vulkan does not sample ASTC,
/// so uploading the blocks would still draw the flat colour. Godot's CPU decoder
/// (<c>Image.Decompress</c>) and the editor BC7 encoder (<c>Image.Compress</c>) do the work.
/// The long edge is capped at 1024, which is the browser albedo cap. The result is cached.
/// </summary>
public static class TexturePrep
{
    public const int MaxEdge = 1024;

    public static bool Apply(ImportedField field)
    {
        bool changed = false;
        foreach (var actor in field.Actors)
        {
            if (actor.Textures.Count == 0) continue;
            var names = actor.Textures.Keys.ToList();
            foreach (string name in names)
            {
                var image = actor.Textures[name];
                if (image.Codec != GpuCodec.Astc) continue;
                var next = Transcode(image, field.Report);
                if (next is null)
                {
                    actor.Textures.Remove(name);
                    changed = true;
                    continue;
                }
                if (!ReferenceEquals(next, image))
                {
                    actor.Textures[name] = next;
                    changed = true;
                }
            }
        }
        if (field.TerrainAlbedo.Any(image => image.Codec == GpuCodec.Astc))
        {
            var remap = new int[field.TerrainAlbedo.Count];
            var next = new List<CompressedTexture>(field.TerrainAlbedo.Count);
            for (int i = 0; i < field.TerrainAlbedo.Count; i++)
            {
                var image = field.TerrainAlbedo[i];
                GD.Print($"terrain albedo {i + 1}/{field.TerrainAlbedo.Count} {image.Name}");
                var done = image.Codec == GpuCodec.Astc ? Transcode(image, field.Report) : image;
                if (done is null)
                {
                    remap[i] = -1;
                    continue;
                }
                remap[i] = next.Count;
                next.Add(done);
            }
            if (field.Terrain is { MaterialLayers: { } layers })
            {
                for (int v = 0; v < layers.Count; v++)
                {
                    int layer = layers[v];
                    layers[v] = layer >= 0 && layer < remap.Length ? remap[layer] : -1;
                }
            }
            field.TerrainAlbedo = next;
            changed = true;
            if (next.Count == 0)
                field.Report.AddReason("MaterialAlb ASTC did not transcode; terrain stays the flat colour");
            else
                field.Report.AddReason($"terrain albedo transcoded {next.Count} layers to {next[0].Codec}, long edge {MaxEdge}");
        }
        RecomputeBc7(field);
        return changed;
    }

    public static string Probe()
    {
        var block = VoidExtent(1, 1, 1, 1);
        var picture = Image.CreateFromData(8, 8, false, Image.Format.Astc8X8, block);
        if (picture is null || picture.IsEmpty()) return "astc probe: CreateFromData rejected a void-extent block";
        if (picture.Decompress() != Error.Ok) return "astc probe: Decompress returned unavailable";
        var pixel = picture.GetPixel(0, 0);
        if (pixel.R < 0.9f) return $"astc probe: decompressed pixel {pixel.R:0.00} was not white";
        picture.GenerateMipmaps();
        if (picture.Compress(Image.CompressMode.Bptc, Image.CompressSource.Srgb) != Error.Ok)
            return "astc probe: decompressed, BC7 compress unavailable (editor build required)";
        int expected = MipChain.Expected(8, 8, 4, 4, 16, false);
        int got = picture.GetData().Length;
        return got == expected
            ? $"astc probe: decompressed and BC7 {got} bytes"
            : $"astc probe: BC7 data {got} bytes, chain formula {expected}";
    }

    static CompressedTexture? Transcode(CompressedTexture image, LoadReport report)
    {
        var format = image.BlockW == 8 && image.BlockH == 8 ? Image.Format.Astc8X8
            : image.BlockW == 4 && image.BlockH == 4 ? Image.Format.Astc4X4
            : (Image.Format?)null;
        if (format is null)
        {
            report.AddReason($"{image.Name}: ASTC {image.BlockW}x{image.BlockH} is not transcoded");
            return null;
        }
        int mip0 = MipChain.LevelBytes(image.Width, image.Height, image.BlockW, image.BlockH, image.BytesPerBlock, false);
        if (image.Blocks.Length < mip0)
        {
            report.AddReason($"{image.Name}: ASTC mip 0 is {image.Blocks.Length} bytes, needs {mip0}");
            return null;
        }
        var picture = Image.CreateFromData(image.Width, image.Height, false, format.Value, image.Blocks.AsSpan(0, mip0).ToArray());
        if (picture is null || picture.IsEmpty() || picture.Decompress() != Error.Ok)
        {
            report.AddReason($"{image.Name}: ASTC decompress failed");
            return null;
        }
        int edge = Math.Max(picture.GetWidth(), picture.GetHeight());
        if (edge > MaxEdge)
        {
            float scale = MaxEdge / (float)edge;
            int width = Math.Max(4, ((int)(picture.GetWidth() * scale) + 3) & ~3);
            int height = Math.Max(4, ((int)(picture.GetHeight() * scale) + 3) & ~3);
            picture.Resize(width, height, Image.Interpolation.Lanczos);
        }
        else if (picture.GetWidth() % 4 != 0 || picture.GetHeight() % 4 != 0)
        {
            int width = Math.Max(4, (picture.GetWidth() + 3) & ~3);
            int height = Math.Max(4, (picture.GetHeight() + 3) & ~3);
            picture.Resize(width, height, Image.Interpolation.Lanczos);
        }
        picture.GenerateMipmaps();
        var source = image.Srgb ? Image.CompressSource.Srgb : Image.CompressSource.Generic;
        if (picture.Compress(Image.CompressMode.Bptc, source) == Error.Ok && picture.GetFormat() == Image.Format.BptcRgba)
        {
            return new CompressedTexture
            {
                Name = image.Name,
                Width = picture.GetWidth(),
                Height = picture.GetHeight(),
                FormatId = image.Srgb ? 0x2006 : 0x2001,
                Codec = GpuCodec.Bc7,
                Srgb = image.Srgb,
                BlockW = 4,
                BlockH = 4,
                BytesPerBlock = 16,
                Blocks = picture.GetData(),
                Layer = image.Layer,
                Note = $"transcoded from ASTC, long edge {MaxEdge}",
            };
        }
        report.AddReason($"{image.Name}: BC7 compress unavailable, kept RGBA8");
        return new CompressedTexture
        {
            Name = image.Name,
            Width = picture.GetWidth(),
            Height = picture.GetHeight(),
            Codec = GpuCodec.Rgba8,
            Srgb = image.Srgb,
            BlockW = 1,
            BlockH = 1,
            BytesPerBlock = 4,
            Blocks = picture.GetData(),
            Layer = image.Layer,
            Note = "ASTC decompressed; BC7 compress was unavailable",
        };
    }

    static void RecomputeBc7(ImportedField field)
    {
        long bc7 = 0;
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var actor in field.Actors)
            foreach (var (name, image) in actor.Textures)
            {
                if (!seen.Add(actor.ModelPath + "|" + name)) continue;
                if (image.Codec == GpuCodec.Bc7) bc7 += image.Blocks.Length;
            }
        foreach (var image in field.TerrainAlbedo)
            if (image.Codec == GpuCodec.Bc7) bc7 += image.Blocks.Length;
        field.Bc7Bytes = bc7;
    }

    /// <summary>One ASTC 8×8 void-extent block. LDR, every texel the given 0–1 colour.</summary>
    public static byte[] VoidExtent(float r, float g, float b, float a)
    {
        ulong low = 0b111111100UL;
        low |= 0b11UL << 10;
        for (int i = 0; i < 4; i++) low |= 0x1FFFUL << (12 + 13 * i);
        static ushort Channel(float value) => (ushort)Math.Clamp((int)Math.Round(value * 65535f), 0, 65535);
        ulong high = Channel(r) | ((ulong)Channel(g) << 16) | ((ulong)Channel(b) << 32) | ((ulong)Channel(a) << 48);
        var block = new byte[16];
        System.Buffers.Binary.BinaryPrimitives.WriteUInt64LittleEndian(block, low);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt64LittleEndian(block.AsSpan(8), high);
        return block;
    }
}
