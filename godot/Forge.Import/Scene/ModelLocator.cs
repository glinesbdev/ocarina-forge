namespace OcarinaForge.Import;

public sealed class ResolvedModel
{
    public string Path { get; init; } = "";
    public ImportedModel Model { get; init; } = new();
    public List<string> Tried { get; init; } = new();
}

/// <summary>
/// Model lookup order from <c>ocarina_forge/models.py</c>: actor pack ModelInfo, then
/// <c>Model/&lt;Project&gt;.&lt;Fmdb&gt;.bfres</c>, then <c>Model/&lt;Gyaml&gt;.&lt;Gyaml&gt;.bfres</c>.
/// An exact file that exists is not replaced by another fmdb in the same project.
/// </summary>
public static class ModelLocator
{
    public static ResolvedModel? Find(DumpFolder dump, string gyaml, List<string> notes)
    {
        var plan = new List<(string Label, byte[]? Blob, string Relative)>();
        var pairs = new List<(string Project, string Fmdb)>();
        var named = new List<string>();
        string packRel = $"Pack/Actor/{gyaml}.pack.zs";
        if (dump.Exists(packRel) || dump.Exists($"Pack/Actor/{gyaml}.pack"))
        {
            string used = dump.Exists(packRel) ? packRel : $"Pack/Actor/{gyaml}.pack";
            try
            {
                var archive = new SarcArchive(dump.Read(used));
                foreach (string name in archive.Names)
                {
                    var blob = archive.Read(name);
                    if (name.Contains("ModelInfo", StringComparison.Ordinal) || name.EndsWith("engine__component__ModelInfo.bgyml", StringComparison.Ordinal))
                    {
                        if (blob.Length >= 2 && blob[0] == (byte)'Y' && blob[1] == (byte)'B' && Byml.Load(blob) is Dictionary<string, object?> document)
                            pairs.AddRange(Pairs(document));
                    }
                    else if (name.EndsWith(".bfres", StringComparison.OrdinalIgnoreCase) || (blob.Length >= 8 && blob[0] == (byte)'F' && blob[4] == (byte)' '))
                        plan.Add(($"{used}:{name}", blob, ""));
                    foreach (string path in ModelPaths(blob))
                        if (!named.Contains(path)) named.Add(path);
                }
            }
            catch (ImportException exc)
            {
                notes.Add($"{used}: {exc.Message}");
            }
        }
        var files = new List<string>();
        void Add(string relative)
        {
            if (!files.Contains(relative)) files.Add(relative);
        }
        foreach (var (project, fmdb) in pairs)
        {
            string zs = $"Model/{project}.{fmdb}.bfres.zs";
            string raw = $"Model/{project}.{fmdb}.bfres";
            Add(zs);
            Add(raw);
            if (dump.Exists(zs) || dump.Exists(raw)) continue;
            string modelDir = Path.Combine(dump.Romfs, "Model");
            if (!Directory.Exists(modelDir)) continue;
            foreach (string path in Directory.EnumerateFiles(modelDir))
            {
                string file = Path.GetFileName(path);
                if (!file.StartsWith(project + ".", StringComparison.OrdinalIgnoreCase)) continue;
                if (file.Contains(".anim", StringComparison.OrdinalIgnoreCase)) continue;
                if (file.EndsWith(".bfres.mc", StringComparison.OrdinalIgnoreCase) || file.EndsWith(".bfres.mc.zs", StringComparison.OrdinalIgnoreCase))
                    continue;
                if (!file.EndsWith(".bfres", StringComparison.OrdinalIgnoreCase) && !file.EndsWith(".bfres.zs", StringComparison.OrdinalIgnoreCase))
                    continue;
                Add("Model/" + file);
            }
        }
        Add($"Model/{gyaml}.{gyaml}.bfres.zs");
        Add($"Model/{gyaml}.{gyaml}.bfres");
        foreach (string text in named)
            foreach (string variant in PathVariants(text))
                Add(variant);
        Add($"Model/{gyaml}.bfres.zs");
        Add($"Model/{gyaml}.bfres");
        foreach (string relative in files)
        {
            if (!dump.Exists(relative)) continue;
            try
            {
                var models = BfresReader.Read(dump.Read(relative));
                var model = models.FirstOrDefault(item => item.Parts.Count > 0) ?? models.FirstOrDefault();
                if (model is null) continue;
                return new ResolvedModel { Path = relative, Model = model, Tried = files };
            }
            catch (ImportException exc)
            {
                notes.Add($"{relative}: {exc.Message}");
                if (pairs.Any(pair => relative == $"Model/{pair.Project}.{pair.Fmdb}.bfres" || relative == $"Model/{pair.Project}.{pair.Fmdb}.bfres.zs"))
                    break;
            }
        }
        if (plan.Count > 0)
        {
            try
            {
                var models = BfresReader.Read(plan[0].Blob!);
                var model = models.FirstOrDefault(item => item.Parts.Count > 0);
                if (model is not null)
                    return new ResolvedModel { Path = plan[0].Label, Model = model, Tried = files };
            }
            catch (ImportException exc) { notes.Add($"{plan[0].Label}: {exc.Message}"); }
        }
        return null;
    }

    public static CompressedTexture? FindTexture(DumpFolder dump, ImportedModel model, string name, string? actorPack)
    {
        if (name.Length == 0) return null;
        if (model.Images.TryGetValue(name, out var embedded)) return embedded;
        foreach (string relative in TextureCandidates(dump, name))
        {
            try
            {
                var textures = BntxReader.Read(dump.Read(relative));
                var match = textures.FirstOrDefault(item => item.Name.Equals(name, StringComparison.OrdinalIgnoreCase)) ?? textures.FirstOrDefault();
                if (match is not null) return match;
            }
            catch (ImportException) { }
        }
        if (actorPack is not null && (dump.Exists(actorPack) || dump.Exists(actorPack.Replace(".zs", ""))))
        {
            try
            {
                string used = dump.Exists(actorPack) ? actorPack : actorPack.Replace(".zs", "");
                var archive = new SarcArchive(dump.Read(used));
                foreach (string entry in archive.Names)
                {
                    var blob = archive.Read(entry);
                    if (!BntxReader.LooksLike(blob) && !entry.EndsWith(".bntx", StringComparison.OrdinalIgnoreCase)) continue;
                    if (!BntxReader.LooksLike(blob)) continue;
                    var match = BntxReader.Read(blob).FirstOrDefault(item => item.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
                    if (match is not null) return match;
                }
            }
            catch (ImportException) { }
        }
        return null;
    }

    static IEnumerable<string> TextureCandidates(DumpFolder dump, string name)
    {
        string file = Path.GetFileName(name);
        foreach (string suffix in new[] { ".bntx.zs", ".bntx" })
        {
            string direct = "Tex/" + file + suffix;
            if (dump.Exists(direct)) { yield return direct; yield break; }
        }
        string dir = Path.Combine(dump.Romfs, "Tex");
        if (!Directory.Exists(dir)) yield break;
        foreach (string path in Directory.EnumerateFiles(dir))
        {
            string stem = Path.GetFileNameWithoutExtension(path);
            if (stem.EndsWith(".bntx", StringComparison.OrdinalIgnoreCase)) stem = Path.GetFileNameWithoutExtension(stem);
            if (stem.Equals(file, StringComparison.OrdinalIgnoreCase))
            {
                yield return "Tex/" + Path.GetFileName(path);
                yield break;
            }
        }
    }

    static IEnumerable<string> ModelPaths(byte[] blob)
    {
        byte[] needle = System.Text.Encoding.ASCII.GetBytes("Model/");
        for (int i = 0; i + needle.Length + 8 < blob.Length; i++)
        {
            if (blob[i] != (byte)'M') continue;
            bool match = true;
            for (int k = 0; k < needle.Length; k++)
                if (blob[i + k] != needle[k]) { match = false; break; }
            if (!match) continue;
            int end = i;
            while (end < blob.Length && end - i < 200 && IsPathByte(blob[end])) end++;
            string text = System.Text.Encoding.ASCII.GetString(blob, i, end - i);
            if (text.Contains(".bfres", StringComparison.Ordinal) && !text.Contains("..", StringComparison.Ordinal))
                yield return text;
        }
    }

    static bool IsPathByte(byte value) => value is (>= (byte)'A' and <= (byte)'Z') or (>= (byte)'a' and <= (byte)'z') or (>= (byte)'0' and <= (byte)'9') or (byte)'_' or (byte)'.' or (byte)'/' or (byte)'+' or (byte)'-';

    static IEnumerable<string> PathVariants(string text)
    {
        string cleaned = text.Replace('\\', '/').Trim();
        if (cleaned.StartsWith("Work/", StringComparison.Ordinal)) cleaned = cleaned[5..];
        cleaned = cleaned.TrimStart('/');
        if (cleaned.Length == 0 || cleaned.Contains("..", StringComparison.Ordinal)) yield break;
        if (cleaned.EndsWith(".bfres.zs", StringComparison.Ordinal))
        {
            yield return cleaned;
            yield return cleaned[..^3];
        }
        else if (cleaned.EndsWith(".bfres", StringComparison.Ordinal))
        {
            yield return cleaned + ".zs";
            yield return cleaned;
        }
        else
        {
            yield return $"Model/{cleaned}.bfres.zs";
            yield return $"Model/{cleaned}.bfres";
        }
    }

    static List<(string, string)> Pairs(Dictionary<string, object?> document)
    {
        var found = new List<(string, string)>();
        void Add(Dictionary<string, object?> node)
        {
            string project = node.GetValueOrDefault("ModelProjectName") as string ?? node.GetValueOrDefault("ModelName") as string ?? "";
            string fmdb = node.GetValueOrDefault("FmdbName") as string ?? "";
            project = project.Trim();
            fmdb = fmdb.Trim();
            if (project.Length == 0 && fmdb.Length == 0) return;
            if (project.Length == 0) project = fmdb;
            if (fmdb.Length == 0) fmdb = project;
            if (!found.Contains((project, fmdb))) found.Add((project, fmdb));
        }
        Add(document);
        if (document.GetValueOrDefault("SubModels") is List<object?> subs)
        {
            foreach (var item in subs)
                if (item is Dictionary<string, object?> node) Add(node);
        }
        return found;
    }
}
