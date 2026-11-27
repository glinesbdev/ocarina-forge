namespace OcarinaForge.Import;

/// <summary>One compact line of counts, plus the repeated reasons collapsed.</summary>
public sealed class LoadReport
{
    public int ActorsLoaded { get; set; }
    public int ActorsMissing { get; set; }
    public int ActorsSkipped { get; set; }
    public int TexturesOk { get; set; }
    public int TexturesFailed { get; set; }
    public int ShapesFailed { get; set; }
    public Dictionary<string, int> Missing { get; set; } = new(StringComparer.Ordinal);
    public Dictionary<string, int> Reasons { get; set; } = new(StringComparer.Ordinal);

    public void Miss(string name)
    {
        ActorsMissing++;
        Missing[name] = Missing.GetValueOrDefault(name) + 1;
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
            $"load textures ok {TexturesOk}, failed {TexturesFailed}",
            $"load shapes failed {ShapesFailed}",
        };
        foreach (var (name, count) in Missing.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal).Take(8))
            lines.Add($"load missing {name} x{count}");
        foreach (var (reason, count) in Reasons.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal).Take(8))
            lines.Add($"load reason {reason} x{count}");
        return string.Join('\n', lines);
    }
}
