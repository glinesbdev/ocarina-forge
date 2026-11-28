namespace OcarinaForge.Import;

/// <summary>One compact line of counts, plus the repeated reasons collapsed.</summary>
public sealed class LoadReport
{
    public int ActorsLoaded { get; set; }
    public int ActorsMissing { get; set; }
    public int ActorsSkipped { get; set; }
    public int TexturesOk { get; set; }
    public int TexturesFailed { get; set; }
    public int TexturesMissing { get; set; }
    public int MaterialsUnbound { get; set; }
    public int ShapesFailed { get; set; }
    public int ShapesOmitted { get; set; }
    public Dictionary<string, int> Missing { get; set; } = new(StringComparer.Ordinal);
    public Dictionary<string, int> Omitted { get; set; } = new(StringComparer.Ordinal);
    public Dictionary<string, int> MissingTextures { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, int> Reasons { get; set; } = new(StringComparer.Ordinal);

    public void Miss(string name)
    {
        ActorsMissing++;
        Missing[name] = Missing.GetValueOrDefault(name) + 1;
    }

    public void MissTexture(string name)
    {
        TexturesMissing++;
        MissingTextures[name] = MissingTextures.GetValueOrDefault(name) + 1;
    }

    public void NoteOmitted(string name)
    {
        ShapesOmitted++;
        Omitted[name] = Omitted.GetValueOrDefault(name) + 1;
    }

    public void AddReason(string reason)
    {
        if (reason.Length == 0) return;
        Reasons[reason] = Reasons.GetValueOrDefault(reason) + 1;
    }

    public string Format()
    {
        var lines = new List<string>
        {
            $"load actors loaded {ActorsLoaded}, missing {ActorsMissing}, skipped {ActorsSkipped}",
            $"load textures ok {TexturesOk}, failed {TexturesFailed}, missing {TexturesMissing}",
            $"load materials unbound {MaterialsUnbound}",
            $"load shapes failed {ShapesFailed}",
            $"load omitted {ShapesOmitted}",
        };
        foreach (var (name, count) in Missing.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal).Take(8))
            lines.Add($"load missing {name} x{count}");
        foreach (var (name, count) in MissingTextures.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal).Take(8))
            lines.Add($"load missing texture {name} x{count}");
        foreach (var (name, count) in Omitted.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal).Take(8))
            lines.Add($"load omitted {name} x{count}");
        foreach (var (reason, count) in Reasons.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal).Take(8))
            lines.Add($"load reason {reason} x{count}");
        return string.Join('\n', lines);
    }
}
