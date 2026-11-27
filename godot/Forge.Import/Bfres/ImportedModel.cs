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
    public string Material { get; init; } = "";
    public int MaterialIndex { get; init; } = -1;
    public string Texture { get; init; } = "";
    public string OverlayTexture { get; init; } = "";
    public string AlbedoSampler { get; init; } = "";
    public string Alpha { get; init; } = "opaque";
    public double Cutoff { get; init; } = 0.5;
    public bool DoubleSided { get; init; } = true;
    public List<(string Sampler, string Texture)> Binds { get; init; } = new();
    public string OmitReason { get; init; } = "";
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
        ("blackfade", "fade"), ("fadeboard", "fade"), ("blackborad", "board"), ("blackboard", "board"),
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

    public static bool IsWater(string shape, string material)
    {
        string low = $"{shape} {material}".ToLowerInvariant();
        return low.Contains("water", StringComparison.Ordinal) || low.Contains("flow", StringComparison.Ordinal);
    }
}
