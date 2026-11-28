using Godot;
using OcarinaForge.Import;

namespace OcarinaForge.Generated;

/// <summary>
/// Same ASTC to BC7 step as the viewer. AMD desktop Vulkan does not sample ASTC.
/// <c>Image.Compress</c> needs the Godot editor binary, including <c>--headless</c>.
/// </summary>
public static class AstcTranscode
{
    public const int MaxEdge = 1024;

    public static void Apply(ImportedField field)
    {
        var converted = new Dictionary<CompressedTexture, CompressedTexture?>();
        foreach (var actor in field.Actors)
        {
            if (actor.Textures.Count == 0) continue;
            foreach (string name in actor.Textures.Keys.ToList())
            {
                var image = actor.Textures[name];
                if (image.Codec != GpuCodec.Astc) continue;
                if (!converted.TryGetValue(image, out var next))
                    converted[image] = next = Transcode(image, field.Report);
                if (next is null) actor.Textures.Remove(name);
                else actor.Textures[name] = next;
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
            void Remap(List<int>? layers)
            {
                if (layers is null) return;
                for (int v = 0; v < layers.Count; v++)
                {
                    int layer = layers[v];
                    layers[v] = layer >= 0 && layer < remap.Length ? remap[layer] : -1;
                }
            }
            Remap(field.Terrain?.MaterialLayers);
            Remap(field.Terrain?.MaterialLayerB);
            field.TerrainAlbedo = next;
            if (next.Count == 0)
                field.Report.AddReason("MaterialAlb ASTC did not transcode; terrain stays the flat colour");
            else
                field.Report.AddReason($"terrain albedo transcoded {next.Count} layers to {next[0].Codec}, long edge {MaxEdge}");
        }
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

    internal static CompressedTexture? Transcode(CompressedTexture image, LoadReport report)
    {
        var format = image.BlockW == 8 && image.BlockH == 8 ? Image.Format.Astc8X8
            : image.BlockW == 4 && image.BlockH == 4 ? Image.Format.Astc4X4
            : (Image.Format?)null;
        int mip0 = MipChain.LevelBytes(image.Width, image.Height, image.BlockW, image.BlockH, image.BytesPerBlock, false);
        if (image.Blocks.Length < mip0)
        {
            report.AddReason($"{image.Name}: ASTC mip 0 is {image.Blocks.Length} bytes, needs {mip0}");
            return null;
        }
        Image? picture;
        try
        {
            picture = format is null
                ? Image.CreateFromData(image.Width, image.Height, false, Image.Format.Rgba8, AstcPixels.DecodeMipZero(image))
                : Image.CreateFromData(image.Width, image.Height, false, format.Value, image.Blocks.AsSpan(0, mip0).ToArray());
        }
        catch (ArgumentException exc)
        {
            report.AddReason($"{image.Name}: ASTC decode failed: {exc.Message}");
            return null;
        }
        if (picture is null || picture.IsEmpty() || (picture.IsCompressed() && picture.Decompress() != Error.Ok))
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
}
