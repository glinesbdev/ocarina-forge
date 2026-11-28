namespace OcarinaForge.Import;

public sealed record CatalogModelSource(string Path, string? Actor = null, string? ActorPack = null)
{
    public string File => Path.Split('#')[^1].Split('/')[^1];

    public byte[] Read(DumpFolder dump)
    {
        int separator = Path.IndexOf('#');
        if (separator < 0) return dump.Read(Path);
        var archive = new SarcArchive(dump.Read(Path[..separator]));
        return ZstdFrames.Decompress(archive.Read(Path[(separator + 1)..]), dump.Dictionaries);
    }
}

/// <summary>Enumerates model containers independently of banc placement.</summary>
public static class ModelCatalog
{
    public static IEnumerable<CatalogModelSource> Discover(DumpFolder dump, string prefix, List<string> notes)
    {
        var aliases = new Dictionary<string, (string Actor, string Pack)>(StringComparer.OrdinalIgnoreCase);
        var members = new List<CatalogModelSource>();
        var packs = dump.Files("Pack/Actor", "*").Where(path =>
                path.EndsWith(".pack.zs", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".pack", StringComparison.OrdinalIgnoreCase))
            .Order(StringComparer.Ordinal).ToArray();
        foreach (string pack in packs)
        {
            if (pack.EndsWith(".pack", StringComparison.OrdinalIgnoreCase) && dump.Exists(pack + ".zs")) continue;
            string actor = System.IO.Path.GetFileName(pack);
            actor = actor[..actor.LastIndexOf(".pack", StringComparison.OrdinalIgnoreCase)];
            try
            {
                var archive = new SarcArchive(dump.Read(pack));
                foreach (string member in archive.Names)
                {
                    if (IsModel(member))
                    {
                        members.Add(new CatalogModelSource(pack + "#" + member, actor, pack));
                        continue;
                    }
                    if (!member.EndsWith(".engine__component__ModelInfo.bgyml", StringComparison.OrdinalIgnoreCase))
                        continue;
                    try
                    {
                        if (Byml.Load(archive.Read(member)) is not Dictionary<string, object?> document) continue;
                        foreach (var (project, fmdb) in ModelLocator.Pairs(document))
                        {
                            string file = $"{project}.{fmdb}.bfres";
                            if (!aliases.TryGetValue(file, out var current) || actor.Equals(fmdb, StringComparison.OrdinalIgnoreCase)
                                && !current.Actor.Equals(fmdb, StringComparison.OrdinalIgnoreCase))
                                aliases[file] = (actor, pack);
                        }
                    }
                    catch (ImportException exc) { notes.Add($"{pack}#{member}: {exc.Message}"); }
                }
            }
            catch (ImportException exc) { notes.Add($"{pack}: {exc.Message}"); }
        }

        string modelDirectory = System.IO.Path.Combine(dump.Romfs, "Model");
        var loose = Directory.Exists(modelDirectory)
            ? Directory.EnumerateFiles(modelDirectory, "*", SearchOption.AllDirectories)
                .Where(IsModel).Select(path => System.IO.Path.GetRelativePath(dump.Romfs, path).Replace('\\', '/'))
            : [];
        foreach (string path in loose.Order(StringComparer.Ordinal))
        {
            if (!path.EndsWith(".zs", StringComparison.OrdinalIgnoreCase) && dump.Exists(path + ".zs")) continue;
            string file = System.IO.Path.GetFileName(path);
            string baseFile = file.EndsWith(".zs", StringComparison.OrdinalIgnoreCase) ? file[..^3] : file;
            var alias = path.Equals("Model/" + file, StringComparison.OrdinalIgnoreCase)
                ? aliases.GetValueOrDefault(baseFile) : default;
            if (Matches(file, alias.Actor, prefix)) yield return new CatalogModelSource(path, alias.Actor, alias.Pack);
        }
        foreach (var source in members.OrderBy(item => item.Path, StringComparer.Ordinal))
            if (Matches(source.File, source.Actor, prefix)) yield return source;
    }

    static bool Matches(string file, string? actor, string prefix) =>
        file.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
        || actor?.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) == true;

    static bool IsModel(string path)
    {
        string file = System.IO.Path.GetFileName(path);
        if (file.EndsWith(".zs", StringComparison.OrdinalIgnoreCase)) file = file[..^3];
        if (file.EndsWith(".mc", StringComparison.OrdinalIgnoreCase)) file = file[..^3];
        if (!file.EndsWith(".bfres", StringComparison.OrdinalIgnoreCase)) return false;
        string stem = file[..^6];
        return !stem.EndsWith(".anim", StringComparison.OrdinalIgnoreCase)
            && !stem.EndsWith(".Skeleton", StringComparison.OrdinalIgnoreCase)
            && !stem.Contains(".Tex", StringComparison.OrdinalIgnoreCase)
            && !stem.EndsWith("_Tex", StringComparison.OrdinalIgnoreCase);
    }
}
