namespace OcarinaForge.Import;

/// <summary>
/// Texture lookup from <c>ocarina_forge/models.py</c>. Longshot <c>content.model</c> serves a
/// texture as <c>Tex/&lt;name&gt;.bntx</c> and does not name sampler slots. Retail actors also
/// keep the images in <c>Model/&lt;prefix&gt;.Tex.bfres</c> and in actor packs. A loose
/// <c>Tex/&lt;name&gt;.bntx.zs</c> still wins.
/// </summary>
public sealed class TextureLibrary
{
    readonly DumpFolder _dump;
    Dictionary<string, string>? _loose;
    readonly Dictionary<string, Dictionary<string, string>> _packs = new(StringComparer.OrdinalIgnoreCase);
    readonly Dictionary<string, SarcArchive?> _archives = new(StringComparer.OrdinalIgnoreCase);
    readonly Dictionary<string, Dictionary<string, CompressedTexture>> _companions = new(StringComparer.OrdinalIgnoreCase);
    string[]? _actorPacks;
    string[]? _sharedPacks;

    public TextureLibrary(DumpFolder dump) => _dump = dump;

    /// <summary>Romfs path of the last texture <see cref="Find"/> returned. A pack member is <c>pack#member</c>.</summary>
    public string LastSource { get; private set; } = "";

    public List<string> CompanionPaths(string modelPath, string gyaml) => CompanionFiles(modelPath, gyaml).ToList();

    public void AttachCompanions(ImportedModel model, string modelPath, string gyaml)
    {
        foreach (string relative in CompanionFiles(modelPath, gyaml))
        {
            if (!_companions.TryGetValue(relative, out var images))
            {
                images = new Dictionary<string, CompressedTexture>(StringComparer.OrdinalIgnoreCase);
                _companions[relative] = images;
                try
                {
                    foreach (var parsed in BfresReader.Read(_dump.Read(relative)))
                        foreach (var (name, image) in parsed.Images)
                            images.TryAdd(name, image);
                }
                catch (ImportException) { }
            }
            foreach (var (name, image) in images)
            {
                if (model.Images.TryAdd(name, image))
                    model.ImageSources[name] = relative;
            }
        }
    }

    public CompressedTexture? Find(ImportedModel model, string name, IReadOnlyList<string> hints)
    {
        LastSource = "";
        if (name.Length == 0) return null;
        var image = FromImages(model.Images, name);
        if (image is not null)
        {
            if (!model.ImageSources.TryGetValue(image.Name, out var source))
                model.ImageSources.TryGetValue(name, out source);
            LastSource = source ?? "";
            return image;
        }
        foreach (string relative in TexCandidates(name))
        {
            if (!_dump.Exists(relative)) continue;
            image = ReadFile(relative, name);
            if (image is not null) return image;
        }
        var loose = Loose();
        foreach (string key in StemKeys(Stem(name)))
        {
            if (!loose.TryGetValue(key, out var path)) continue;
            image = ReadFile(path, name);
            if (image is not null) return image;
        }
        foreach (string pack in ActorPacks(hints).Concat(SharedPacks()))
        {
            var index = PackIndex(pack);
            foreach (string key in StemKeys(Stem(name)))
            {
                if (!index.TryGetValue(key, out var member)) continue;
                image = ReadMember(pack, member, name);
                if (image is not null) return image;
            }
        }
        foreach (string pack in NamePacks(name))
        {
            var index = PackIndex(pack);
            foreach (string key in StemKeys(Stem(name)))
            {
                if (!index.TryGetValue(key, out var member)) continue;
                image = ReadMember(pack, member, name);
                if (image is not null) return image;
            }
        }
        return Bundle(name, hints);
    }

    /// <summary>
    /// A BNTX or <c>.Tex.bfres</c> whose file stem sits inside the texture name.
    /// <c>CmnTex_Rock.bntx</c> holds <c>CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb</c>.
    /// The exact stem is already tried. This is <c>models.py</c> <c>_search_bundles</c>
    /// plus a companion whose prefix is the texture name, not the actor.
    /// </summary>
    CompressedTexture? Bundle(string name, IReadOnlyList<string> hints)
    {
        string wanted = Stem(name).ToLowerInvariant();
        if (wanted.Length < 6) return null;
        var hits = new List<(int Rank, string Kind, string Pack, string Member)>();
        foreach (var (stem, path) in Loose())
        {
            if (!StemHits(stem, wanted)) continue;
            hits.Add((stem.Length, "file", path, ""));
        }
        foreach (string pack in ActorPacks(hints).Concat(SharedPacks()).Concat(NamePacks(name)))
        {
            foreach (var (stem, member) in PackIndex(pack))
            {
                if (!StemHits(stem, wanted)) continue;
                hits.Add((stem.Length, "pack", pack, member));
            }
        }
        foreach (string relative in TextureArchives(wanted))
            hits.Add((Stem(relative).Length, "bfres", relative, ""));
        hits.Sort((a, b) => b.Rank.CompareTo(a.Rank));
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int opened = 0;
        foreach (var hit in hits)
        {
            string key = hit.Kind + "\n" + hit.Pack + "\n" + hit.Member;
            if (!seen.Add(key)) continue;
            if (opened++ >= 8) break;
            CompressedTexture? image = hit.Kind switch
            {
                "file" => ReadFile(hit.Pack, name, named: true),
                "pack" => ReadMember(hit.Pack, hit.Member, name, named: true),
                _ => FromArchive(hit.Pack, name),
            };
            if (image is not null) return image;
        }
        return null;
    }

    public static bool StemHits(string fileStem, string wantedStem)
    {
        string file = fileStem.ToLowerInvariant();
        string wanted = wantedStem.ToLowerInvariant();
        if (file.Length < 6 || wanted.Length < 6 || file.Equals(wanted, StringComparison.Ordinal)) return false;
        return wanted.Contains(file, StringComparison.Ordinal) || file.Contains(wanted, StringComparison.Ordinal);
    }

    string[]? _texBfres;

    IEnumerable<string> TextureArchives(string wanted)
    {
        if (_texBfres is null)
        {
            string dir = Path.Combine(_dump.Romfs, "Model");
            var found = new List<string>();
            if (Directory.Exists(dir))
            {
                foreach (string path in Directory.EnumerateFiles(dir))
                {
                    string file = Path.GetFileName(path);
                    if (IsCompanion(file)) found.Add("Model/" + file);
                }
            }
            _texBfres = found.ToArray();
        }
        foreach (string relative in _texBfres)
        {
            string stem = Stem(Path.GetFileName(relative)).ToLowerInvariant();
            stem = stem.Replace(".tex", "", StringComparison.Ordinal).Replace("_tex", "", StringComparison.Ordinal);
            if (stem.Length >= 6 && (StemHits(stem, wanted) || wanted.StartsWith(stem, StringComparison.Ordinal)))
                yield return relative;
        }
    }

    CompressedTexture? FromArchive(string relative, string name)
    {
        if (!_companions.TryGetValue(relative, out var images))
        {
            images = new Dictionary<string, CompressedTexture>(StringComparer.OrdinalIgnoreCase);
            _companions[relative] = images;
            try
            {
                foreach (var parsed in BfresReader.Read(_dump.Read(relative)))
                    foreach (var (imageName, image) in parsed.Images)
                        images.TryAdd(imageName, image);
            }
            catch (ImportException) { }
        }
        var match = PickNamed(images.Values, name);
        if (match is not null) LastSource = relative.Replace('\\', '/');
        return match;
    }

    public static List<string> Hints(string gyaml, string modelPath, string modelName)
    {
        var hints = new List<string>();
        void Add(string token)
        {
            if (token.Length == 0 || token.Contains('/') || token is "Model" or "Tex" or "Banc" or "Pack") return;
            if (!hints.Contains(token)) hints.Add(token);
        }
        Add(gyaml);
        Add(modelName);
        string file = modelPath.Replace('\\', '/').Split('/').Last();
        foreach (string suffix in new[] { ".bfres.zs", ".bfres.mc", ".bfres" })
        {
            if (file.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
            {
                file = file[..^suffix.Length];
                break;
            }
        }
        foreach (string token in file.Split('.')) Add(token);
        var extra = new List<string>();
        foreach (string token in hints.ToList())
        {
            var parts = token.Split('_');
            for (int end = parts.Length - 1; end > 1; end--)
            {
                string prefix = string.Join('_', parts.Take(end));
                if (prefix.Length >= 8 && !hints.Contains(prefix) && !extra.Contains(prefix)) extra.Add(prefix);
            }
        }
        extra.Sort((a, b) => b.Length.CompareTo(a.Length));
        hints.AddRange(extra);
        return hints;
    }

    IEnumerable<string> CompanionFiles(string modelPath, string gyaml)
    {
        var found = new List<string>();
        void Add(string relative)
        {
            if (relative.Length == 0 || found.Contains(relative) || relative.Equals(modelPath, StringComparison.OrdinalIgnoreCase)) return;
            if (_dump.Exists(relative)) found.Add(relative);
        }
        foreach (string prefix in Hints(gyaml, modelPath, ""))
        {
            Add($"Model/{prefix}.Tex.bfres.zs");
            Add($"Model/{prefix}.Tex.bfres");
            Add($"Model/{prefix}_Tex.bfres.zs");
            Add($"Model/{prefix}_Tex.bfres");
        }
        string dir = Path.Combine(_dump.Romfs, "Model");
        if (!Directory.Exists(dir)) return found;
        var prefixes = Hints(gyaml, modelPath, "").Select(item => item.ToLowerInvariant()).ToList();
        foreach (string path in Directory.EnumerateFiles(dir))
        {
            string file = Path.GetFileName(path);
            if (!IsCompanion(file)) continue;
            string stem = file;
            foreach (string suffix in new[] { ".bfres.zs", ".bfres" })
            {
                if (stem.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                {
                    stem = stem[..^suffix.Length];
                    break;
                }
            }
            if (prefixes.Any(prefix => stem.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)))
                Add("Model/" + file);
        }
        return found;
    }

    static bool IsCompanion(string filename)
    {
        string lower = filename.ToLowerInvariant();
        if (lower.Contains(".anim.", StringComparison.Ordinal) || lower.Contains(".bfres.mc", StringComparison.Ordinal)) return false;
        if (!lower.EndsWith(".bfres", StringComparison.Ordinal) && !lower.EndsWith(".bfres.zs", StringComparison.Ordinal)) return false;
        string stem = lower;
        foreach (string suffix in new[] { ".bfres.zs", ".bfres" })
        {
            if (stem.EndsWith(suffix, StringComparison.Ordinal))
            {
                stem = stem[..^suffix.Length];
                break;
            }
        }
        return stem.Contains(".tex", StringComparison.Ordinal) || stem.EndsWith("_tex", StringComparison.Ordinal);
    }

    Dictionary<string, string> Loose()
    {
        if (_loose is not null) return _loose;
        var grouped = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        if (Directory.Exists(_dump.Romfs))
        {
            foreach (string path in Directory.EnumerateFiles(_dump.Romfs, "*.*", SearchOption.AllDirectories))
            {
                string file = Path.GetFileName(path);
                if (!file.EndsWith(".bntx", StringComparison.OrdinalIgnoreCase) && !file.EndsWith(".bntx.zs", StringComparison.OrdinalIgnoreCase))
                    continue;
                string relative = Path.GetRelativePath(_dump.Romfs, path).Replace('\\', '/');
                string stem = Stem(file);
                if (!grouped.TryGetValue(stem, out var list))
                {
                    list = new List<string>();
                    grouped[stem] = list;
                }
                list.Add(relative);
            }
        }
        _loose = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var (stem, paths) in grouped)
            _loose[stem] = Prefer(paths);
        return _loose;
    }

    static string Prefer(List<string> paths)
    {
        return paths
            .Select(path => (path, Score(path)))
            .OrderBy(item => item.Item2.Rank)
            .ThenBy(item => item.Item2.Length)
            .ThenBy(item => item.path, StringComparer.OrdinalIgnoreCase)
            .First().path;
    }

    static (int Rank, int Length) Score(string path)
    {
        string name = path.Split('/').Last().ToLowerInvariant();
        bool top = path.StartsWith("Tex/", StringComparison.OrdinalIgnoreCase) && path.Count(item => item == '/') == 1;
        bool inTex = top || path.StartsWith("Tex/", StringComparison.OrdinalIgnoreCase) || path.Contains("/Tex/", StringComparison.OrdinalIgnoreCase);
        bool zs = name.EndsWith(".bntx.zs", StringComparison.Ordinal);
        int rank = top && zs ? 0 : top ? 1 : inTex && zs ? 2 : inTex ? 3 : path.StartsWith("Banc/", StringComparison.OrdinalIgnoreCase) && zs ? 4 : zs ? 5 : 6;
        return (rank, path.Length);
    }

    IEnumerable<string> ActorPacks(IReadOnlyList<string> hints)
    {
        foreach (string token in hints)
        {
            foreach (string suffix in new[] { ".pack.zs", ".pack" })
            {
                string relative = $"Pack/Actor/{token}{suffix}";
                if (_dump.Exists(relative)) yield return relative;
            }
        }
    }

    IEnumerable<string> SharedPacks()
    {
        if (_sharedPacks is not null)
        {
            foreach (string pack in _sharedPacks) yield return pack;
            yield break;
        }
        string dir = Path.Combine(_dump.Romfs, "Pack");
        var found = new List<string>();
        if (Directory.Exists(dir))
        {
            foreach (string path in Directory.EnumerateFiles(dir))
            {
                string file = Path.GetFileName(path);
                if (!file.EndsWith(".pack.zs", StringComparison.OrdinalIgnoreCase) && !file.EndsWith(".pack", StringComparison.OrdinalIgnoreCase))
                    continue;
                if (file.Equals("ZsDic.pack.zs", StringComparison.OrdinalIgnoreCase) || file.Equals("ZsDic.pack", StringComparison.OrdinalIgnoreCase))
                    continue;
                found.Add("Pack/" + file);
            }
        }
        found.Sort((a, b) => RankPack(a).CompareTo(RankPack(b)));
        _sharedPacks = found.ToArray();
        foreach (string pack in _sharedPacks) yield return pack;
    }

    static int RankPack(string relative)
    {
        string lower = relative.ToLowerInvariant();
        if (lower.Contains("residentcommon", StringComparison.Ordinal)) return 0;
        if (lower.Contains("bootup", StringComparison.Ordinal)) return 1;
        return 2;
    }

    IEnumerable<string> NamePacks(string name)
    {
        string stem = Stem(name).ToLowerInvariant();
        if (stem.Length < 8) yield break;
        foreach (var (packStem, relative) in ActorPackStems())
            if (stem.Contains(packStem, StringComparison.Ordinal)) yield return relative;
    }

    IEnumerable<(string Stem, string Relative)> ActorPackStems()
    {
        if (_actorPacks is null)
        {
            string dir = Path.Combine(_dump.Romfs, "Pack", "Actor");
            var rows = new List<string>();
            if (Directory.Exists(dir))
            {
                foreach (string path in Directory.EnumerateFiles(dir))
                {
                    string file = Path.GetFileName(path);
                    string stem = file.ToLowerInvariant();
                    foreach (string suffix in new[] { ".pack.zs", ".pack" })
                    {
                        if (stem.EndsWith(suffix, StringComparison.Ordinal))
                        {
                            stem = stem[..^suffix.Length];
                            break;
                        }
                    }
                    if (stem.Length < 8) continue;
                    rows.Add(stem + "\nPack/Actor/" + file);
                }
            }
            rows.Sort((a, b) => b.Split('\n')[0].Length.CompareTo(a.Split('\n')[0].Length));
            _actorPacks = rows.ToArray();
        }
        foreach (string row in _actorPacks)
        {
            var parts = row.Split('\n');
            yield return (parts[0], parts[1]);
        }
    }

    Dictionary<string, string> PackIndex(string pack)
    {
        if (_packs.TryGetValue(pack, out var cached)) return cached;
        var index = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        _packs[pack] = index;
        try
        {
            var archive = Archive(pack);
            if (archive is null) return index;
            foreach (string member in archive.Names)
            {
                string file = member.Replace('\\', '/').Split('/').Last();
                if (!file.EndsWith(".bntx", StringComparison.OrdinalIgnoreCase) && !file.EndsWith(".bntx.zs", StringComparison.OrdinalIgnoreCase))
                    continue;
                index.TryAdd(Stem(file), member);
            }
        }
        catch (ImportException) { }
        return index;
    }

    CompressedTexture? ReadFile(string relative, string name, bool named = false)
    {
        try
        {
            var images = BntxReader.Read(_dump.Read(relative));
            var image = named ? PickNamed(images, name) : Pick(images, name);
            if (image is not null) LastSource = relative.Replace('\\', '/');
            return image;
        }
        catch (ImportException) { return null; }
    }

    CompressedTexture? ReadMember(string pack, string member, string name, bool named = false)
    {
        try
        {
            var archive = Archive(pack);
            if (archive is null) return null;
            var blob = archive.Read(member);
            if (!BntxReader.LooksLike(blob))
                blob = ZstdFrames.Decompress(blob, _dump.Dictionaries);
            if (!BntxReader.LooksLike(blob)) return null;
            var images = BntxReader.Read(blob);
            var image = named ? PickNamed(images, name) : Pick(images, name);
            if (image is not null) LastSource = pack.Replace('\\', '/') + "#" + member.Replace('\\', '/');
            return image;
        }
        catch (ImportException) { return null; }
    }

    SarcArchive? Archive(string pack)
    {
        if (_archives.TryGetValue(pack, out var cached)) return cached;
        try
        {
            var archive = new SarcArchive(_dump.Read(pack));
            _archives[pack] = archive;
            return archive;
        }
        catch (ImportException)
        {
            _archives[pack] = null;
            return null;
        }
    }

    static CompressedTexture? FromImages(Dictionary<string, CompressedTexture> images, string name)
    {
        if (images.TryGetValue(name, out var direct)) return direct;
        foreach (string key in StemKeys(Stem(name)))
        {
            foreach (var (imageName, image) in images)
                if (Stem(imageName).Equals(key, StringComparison.OrdinalIgnoreCase)) return image;
        }
        return null;
    }

    static CompressedTexture? Pick(IReadOnlyList<CompressedTexture> images, string name)
    {
        var match = images.FirstOrDefault(item => item.Name.Equals(name, StringComparison.OrdinalIgnoreCase) || Stem(item.Name).Equals(Stem(name), StringComparison.OrdinalIgnoreCase));
        if (match is not null) return match;
        foreach (string key in StemKeys(Stem(name)))
        {
            match = images.FirstOrDefault(item => Stem(item.Name).Equals(key, StringComparison.OrdinalIgnoreCase));
            if (match is not null) return match;
        }
        return images.Count == 1 ? images[0] : null;
    }

    static CompressedTexture? PickNamed(IEnumerable<CompressedTexture> images, string name)
    {
        string stem = Stem(name).ToLowerInvariant();
        CompressedTexture? loose = null;
        foreach (var image in images)
        {
            string current = Stem(image.Name).ToLowerInvariant();
            if (current.Length == 0) continue;
            if (current.Equals(stem, StringComparison.Ordinal) || image.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                return image;
            if (loose is null && stem.Contains(current, StringComparison.Ordinal) && current.Contains("alb", StringComparison.Ordinal))
                loose = image;
            if (loose is null && current.Contains(stem, StringComparison.Ordinal) && current.Contains("alb", StringComparison.Ordinal))
                loose = image;
        }
        return loose;
    }

    static IEnumerable<string> TexCandidates(string name)
    {
        string text = name.Trim().Replace('\\', '/').TrimStart('/');
        if (text.StartsWith("Work/", StringComparison.OrdinalIgnoreCase)) text = text[5..];
        if (text.Contains('/', StringComparison.Ordinal))
        {
            string lower = text.ToLowerInvariant();
            if (lower.EndsWith(".bntx.zs", StringComparison.Ordinal))
            {
                yield return text;
                yield return text[..^3];
            }
            else if (lower.EndsWith(".bntx", StringComparison.Ordinal))
            {
                yield return text + ".zs";
                yield return text;
            }
            else
            {
                yield return text + ".bntx.zs";
                yield return text + ".bntx";
            }
        }
        string stem = Stem(text);
        yield return $"Tex/{stem}.bntx.zs";
        yield return $"Tex/{stem}.bntx";
    }

    public static IEnumerable<string> StemKeys(string stem)
    {
        string key = stem.ToLowerInvariant();
        if (key.Length == 0) yield break;
        yield return key;
        if (key.EndsWith("_albedo", StringComparison.Ordinal)) yield return key[..^"_albedo".Length];
        else if (key.EndsWith("_alb", StringComparison.Ordinal)) yield return key[..^"_alb".Length];
        else
        {
            yield return key + "_alb";
            yield return key + "_albedo";
        }
    }

    public static string Stem(string name)
    {
        string text = name.Trim().Replace('\\', '/').TrimStart('/');
        if (text.StartsWith("Work/", StringComparison.OrdinalIgnoreCase)) text = text[5..];
        string stem = text.Split('/').Last();
        string lower = stem.ToLowerInvariant();
        foreach (string suffix in new[] { ".bntx.zs", ".bntx" })
        {
            if (lower.EndsWith(suffix, StringComparison.Ordinal))
            {
                stem = stem[..^suffix.Length];
                break;
            }
        }
        return stem;
    }
}
