namespace OcarinaForge.Import;

public readonly record struct Vec2(double U, double V);
public readonly record struct Vec3(double X, double Y, double Z);

public sealed class ImportedPart
{
    public string Name { get; init; } = "";
    public int Lod { get; init; }
    public List<Vec3> Positions { get; init; } = new();
    public List<int> Indices { get; init; } = new();
    public List<Vec2>? Uvs { get; init; }
    public List<Vec3>? Normals { get; init; }
    /// <summary>MaterialAlb layer per vertex. -1 is the flat earth colour. Null when the mesh has no grid.</summary>
    public List<int>? MaterialLayers { get; init; }
    public string Material { get; init; } = "";
    public int MaterialIndex { get; init; } = -1;
    public string Texture { get; init; } = "";
    public string OverlayTexture { get; init; } = "";
    public string AlbedoSampler { get; init; } = "";
    public string Alpha { get; init; } = "opaque";
    public double Cutoff { get; init; } = 0.5;
    public bool DoubleSided { get; init; } = true;
    public List<(string Sampler, string Texture)> Binds { get; init; } = new();
    public string OmitReason { get; set; } = "";
    /// <summary>Second MaterialAlb index. -1 means there is no blend partner.</summary>
    public List<int>? MaterialLayerB { get; set; }
    /// <summary>Blend toward <see cref="MaterialLayerB"/>, 0 on material A.</summary>
    public List<float>? MaterialBlend { get; set; }
    public string Helper { get; init; } = "";
    public bool Water { get; init; }
}

public sealed class ImportedModel
{
    public string Name { get; init; } = "";
    public List<ImportedPart> Parts { get; init; } = new();
    public Dictionary<string, CompressedTexture> Images { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public List<string> Notes { get; init; } = new();
    public List<string> TextureHints { get; init; } = new();
}

public static class ShapeFilter
{
    static readonly (string Token, string Kind)[] Helpers =
    {
        ("blackfade", "fade"), ("fadeblack", "fade"), ("blackborad", "board"), ("blackboard", "board"),
        ("darkplane", "dark"), ("shadowboard", "shadow"),
    };

    public static string HelperKind(string shape, string material)
    {
        string compact = (shape + material).ToLowerInvariant().Replace("_", "").Replace(" ", "");
        foreach (var (token, kind) in Helpers)
            if (compact.Contains(token, StringComparison.Ordinal)) return kind;
        return "";
    }

    public static string OmitReason(string shape, string material)
    {
        string compact = $"{shape} {material}".ToLowerInvariant().Replace("_", "").Replace(" ", "");
        if (compact.Contains("watervolume", StringComparison.Ordinal)) return "water volume is not drawn";
        if (compact.Contains("bakeshadowproxy", StringComparison.Ordinal)) return "bake shadow proxy is not drawn";
        if (compact.Contains("innermesh", StringComparison.Ordinal)) return "inner mesh is not drawn";
        if (compact.Contains("eyeouter", StringComparison.Ordinal) || compact.Contains("eyeshadow", StringComparison.Ordinal))
            return "eye overlay is not drawn";
        foreach (var (word, reason) in new (string, string)[]
        {
            ("occluder", "occluder is not drawn"),
            ("navmesh", "navmesh proxy is not drawn"),
            ("minimap", "minimap model is not drawn"),
            ("mapicon", "map icon is not drawn"),
            ("sightblock", "sight blocker is not drawn"),
            ("charactersight", "sight blocker is not drawn"),
            ("collision", "collision mesh is not drawn"),
            ("collider", "collision mesh is not drawn"),
            ("impostor", "impostor is not drawn"),
        })
        {
            if (compact.Contains(word, StringComparison.Ordinal)) return reason;
        }
        return "";
    }

    /// <summary>
    /// The same hides as <c>models.py</c>: helper planes, the back water sheet when a front
    /// sheet exists, and a coarse untextured box under a smaller mesh that has UVs.
    /// </summary>
    public static void Hide(List<ImportedPart> parts)
    {
        bool front = parts.Any(part =>
        {
            string low = $"{part.Name} {part.Material}".ToLowerInvariant();
            return low.Contains("front", StringComparison.Ordinal) && (low.Contains("water", StringComparison.Ordinal) || low.Contains("flow", StringComparison.Ordinal));
        });
        foreach (var part in parts)
        {
            if (part.OmitReason.Length > 0) continue;
            if (part.Helper.Length > 0)
            {
                part.OmitReason = $"{part.Helper} helper is not drawn";
                continue;
            }
            string low = $"{part.Name} {part.Material}".ToLowerInvariant();
            if (front && low.Contains("back", StringComparison.Ordinal) && (low.Contains("water", StringComparison.Ordinal) || low.Contains("flow", StringComparison.Ordinal)))
            {
                part.OmitReason = "back water layer is not drawn";
                continue;
            }
            if (BoundsBox(part, parts))
                part.OmitReason = "untextured bounds box is not drawn";
        }
    }

    static bool BoundsBox(ImportedPart part, List<ImportedPart> parts)
    {
        if (part.Uvs is { Count: > 0 } || part.Positions.Count > 24 || Span(part) < 4) return false;
        return parts.Any(other => !ReferenceEquals(other, part) && other.Uvs is { Count: > 0 } && other.Positions.Count > part.Positions.Count);
    }

    static double Span(ImportedPart part)
    {
        if (part.Positions.Count == 0) return 0;
        double minX = part.Positions[0].X, maxX = minX, minY = part.Positions[0].Y, maxY = minY, minZ = part.Positions[0].Z, maxZ = minZ;
        foreach (var point in part.Positions)
        {
            minX = Math.Min(minX, point.X); maxX = Math.Max(maxX, point.X);
            minY = Math.Min(minY, point.Y); maxY = Math.Max(maxY, point.Y);
            minZ = Math.Min(minZ, point.Z); maxZ = Math.Max(maxZ, point.Z);
        }
        return Math.Max(maxX - minX, Math.Max(maxY - minY, maxZ - minZ));
    }

    public static bool IsWater(string shape, string material)
    {
        string low = $"{shape} {material}".ToLowerInvariant();
        return low.Contains("water", StringComparison.Ordinal) || low.Contains("flow", StringComparison.Ordinal);
    }
}
