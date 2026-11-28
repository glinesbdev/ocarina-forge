using System.Text.Json;
using System.Text.Json.Serialization;

namespace OcarinaForge.Project;

public sealed class ManifestEntry
{
    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "";

    [JsonPropertyName("sources")]
    public List<string> Sources { get; set; } = new();

    /// <summary>Hash of the source files and the importer version. Not a hash of the written Godot file.</summary>
    [JsonPropertyName("hash")]
    public string Hash { get; set; } = "";
}

public sealed class ManifestSummary
{
    [JsonPropertyName("geometryBytes")]
    public long GeometryBytes { get; set; }

    [JsonPropertyName("bc7Bytes")]
    public long Bc7Bytes { get; set; }

    [JsonPropertyName("actorsLoaded")]
    public int ActorsLoaded { get; set; }

    [JsonPropertyName("actorsMissing")]
    public int ActorsMissing { get; set; }

    [JsonPropertyName("actorsSkipped")]
    public int ActorsSkipped { get; set; }

    [JsonPropertyName("texturesOk")]
    public int TexturesOk { get; set; }

    [JsonPropertyName("texturesFailed")]
    public int TexturesFailed { get; set; }

    [JsonPropertyName("texturesMissing")]
    public int TexturesMissing { get; set; }

    [JsonPropertyName("materialsUnbound")]
    public int MaterialsUnbound { get; set; }

    [JsonPropertyName("shapesFailed")]
    public int ShapesFailed { get; set; }

    [JsonPropertyName("shapesOmitted")]
    public int ShapesOmitted { get; set; }
}

public sealed class ManifestDocument
{
    [JsonPropertyName("version")]
    public int Version { get; set; } = 1;

    [JsonPropertyName("importer")]
    public string Importer { get; set; } = ProjectPlan.ImporterVersion;

    [JsonPropertyName("dump")]
    public string Dump { get; set; } = "";

    [JsonPropertyName("banc")]
    public string Banc { get; set; } = "";

    [JsonPropertyName("summary")]
    public ManifestSummary Summary { get; set; } = new();

    [JsonPropertyName("files")]
    public Dictionary<string, ManifestEntry> Files { get; set; } = new();
}

public sealed class ProjectRequest
{
    [JsonPropertyName("dump")]
    public string Dump { get; set; } = "";

    [JsonPropertyName("banc")]
    public string Banc { get; set; } = FieldLoader.DefaultBanc;
}

public static class ManifestStore
{
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static ManifestDocument? Load(string projectDir)
    {
        string path = Path.Combine(projectDir, ProjectPaths.Manifest.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(path)) return null;
        return JsonSerializer.Deserialize<ManifestDocument>(File.ReadAllText(path), Json);
    }

    public static void Save(string projectDir, ManifestDocument document)
    {
        string path = Path.Combine(projectDir, ProjectPaths.Manifest.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, JsonSerializer.Serialize(document, Json) + "\n");
    }

    public static ProjectRequest LoadRequest(string projectDir)
    {
        string path = Path.Combine(projectDir, ProjectPaths.Request.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(path)) throw new InvalidOperationException($"missing {path}. Run ocarina-forge-godot new.");
        return JsonSerializer.Deserialize<ProjectRequest>(File.ReadAllText(path), Json)
            ?? throw new InvalidOperationException($"empty {path}");
    }

    public static void SaveRequest(string projectDir, ProjectRequest request)
    {
        string path = Path.Combine(projectDir, ProjectPaths.Request.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, JsonSerializer.Serialize(request, Json) + "\n");
    }

    public static bool Current(ManifestDocument? existing, string resPath, string hash, string projectDir)
    {
        if (existing is null) return false;
        if (!string.Equals(existing.Importer, ProjectPlan.ImporterVersion, StringComparison.Ordinal)) return false;
        if (!existing.Files.TryGetValue(resPath, out var entry)) return false;
        if (!string.Equals(entry.Hash, hash, StringComparison.OrdinalIgnoreCase)) return false;
        return File.Exists(ProjectPaths.Disk(projectDir, resPath));
    }
}
