namespace OcarinaForge.Project;

/// <summary>Stable output layout. Later phases (actors, audio, collision, mod export) add folders beside these.</summary>
public static class ProjectPaths
{
    public const string Manifest = ".ocarina/manifest.json";
    public const string Request = ".ocarina/project.json";
    public const string MapScene = "res://maps/HyruleField.tscn";
    public const string TerrainShader = "res://maps/HyruleField/terrain.gdshader";
    public const string TerrainMesh = "res://maps/HyruleField/terrain.res";
    public const string TerrainMaterial = "res://maps/HyruleField/terrain.tres";
    public const string TerrainArray = "res://maps/HyruleField/albedo.res";
    public const string TerrainOccluder = "res://maps/HyruleField/occluder.res";
    public const string WaterMaterial = "res://materials/water.tres";

    public static string Safe(string name)
    {
        if (string.IsNullOrWhiteSpace(name)) return "unnamed";
        var chars = new char[name.Length];
        int used = 0;
        foreach (char c in name)
        {
            if (char.IsAsciiLetterOrDigit(c) || c is '.' or '_' or '-') chars[used++] = c;
            else if (used == 0 || chars[used - 1] != '_') chars[used++] = '_';
        }
        var text = new string(chars, 0, used).Trim('_', '.');
        if (text.Length == 0) return "unnamed";
        return text.Length > 80 ? text[..80].Trim('_', '.') : text;
    }

    public static string ModelScene(string gyaml)
    {
        string safe = Safe(gyaml);
        return $"res://models/{safe}/{safe}.tscn";
    }

    public static string Mesh(string gyaml, string part, int lod) =>
        $"res://models/{Safe(gyaml)}/meshes/{Safe(part)}_lod{lod}.res";

    public static string Animations(string gyaml) => $"res://models/{Safe(gyaml)}/animations.res";

    public static string Material(string gyaml, string name) =>
        $"res://models/{Safe(gyaml)}/materials/{Safe(name)}.tres";

    public static string Texture(string name) => $"res://textures/{Safe(name)}.res";

    public static string TerrainLayer(int index) => $"res://maps/HyruleField/layer_{index}.png";

    public static string Disk(string projectDir, string resPath)
    {
        string relative = resPath.StartsWith("res://", StringComparison.Ordinal) ? resPath["res://".Length..] : resPath;
        return Path.Combine(projectDir, relative.Replace('/', Path.DirectorySeparatorChar));
    }

    public static bool Drawn(ImportedPart part) =>
        part.OmitReason.Length == 0 && part.Helper.Length == 0 && part.Lod <= 1
        && part.Positions.Count > 0 && part.Indices.Count >= 3;
}
