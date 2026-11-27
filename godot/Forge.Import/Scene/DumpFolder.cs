namespace OcarinaForge.Import;

/// <summary>A dump root: the folder that contains <c>romfs/</c>, or <c>romfs</c> itself.</summary>
public sealed class DumpFolder
{
    public string Root { get; }
    public string Romfs { get; }
    public ZstdDictionaries? Dictionaries { get; }

    DumpFolder(string root, string romfs, ZstdDictionaries? dictionaries)
    {
        Root = root;
        Romfs = romfs;
        Dictionaries = dictionaries;
    }

    public static DumpFolder Open(string path)
    {
        string full = Path.GetFullPath(path);
        if (!Directory.Exists(full))
            throw new ImportException($"dump folder does not exist: {full}");
        string romfs = Directory.Exists(Path.Combine(full, "romfs")) ? Path.Combine(full, "romfs")
            : string.Equals(Path.GetFileName(full), "romfs", StringComparison.OrdinalIgnoreCase) ? full
            : "";
        if (romfs.Length == 0)
            throw new ImportException($"{full} is not a dump root. Pass the folder that contains romfs/, or the romfs folder itself.");
        string dict = Path.Combine(romfs, "Pack", "ZsDic.pack.zs");
        ZstdDictionaries? dictionaries = File.Exists(dict) ? ZstdDictionaries.Load(dict) : null;
        return new DumpFolder(full, romfs, dictionaries);
    }

    public bool Exists(string relative) => File.Exists(Path.Combine(Romfs, relative.Replace('/', Path.DirectorySeparatorChar)));

    public byte[] Read(string relative)
    {
        string path = Path.Combine(Romfs, relative.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(path)) throw new ImportException($"missing {relative}");
        return ZstdFrames.Decompress(File.ReadAllBytes(path), Dictionaries);
    }

    public object? ReadByml(string relative) => Byml.Load(Read(relative));

    public IEnumerable<string> Files(string relativeDirectory, string search)
    {
        string dir = Path.Combine(Romfs, relativeDirectory.Replace('/', Path.DirectorySeparatorChar));
        if (!Directory.Exists(dir)) yield break;
        foreach (string path in Directory.EnumerateFiles(dir, search))
            yield return relativeDirectory.TrimEnd('/') + "/" + Path.GetFileName(path);
    }
}

public sealed class ActorPlacement
{
    public string Gyaml { get; init; } = "";
    public ulong Hash { get; init; }
    public string Name { get; init; } = "";
    public string Source { get; init; } = "";
    public double X { get; init; }
    public double Y { get; init; }
    public double Z { get; init; }
    public double Rx { get; init; }
    public double Ry { get; init; }
    public double Rz { get; init; }
    public double Sx { get; init; } = 1;
    public double Sy { get; init; } = 1;
    public double Sz { get; init; } = 1;
    public bool Draw { get; init; }
    public string SkipReason { get; init; } = "";

    public double[] Matrix() => BindPose.Matrix(X, Y, Z, Rx, Ry, Rz, Sx, Sy, Sz);
}

public static class ActorList
{
    public static List<ActorPlacement> Read(object? document, string source)
    {
        var found = new List<ActorPlacement>();
        if (document is not Dictionary<string, object?> root) return found;
        if (root.GetValueOrDefault("Actors") is not List<object?> actors) return found;
        foreach (var item in actors)
        {
            if (item is not Dictionary<string, object?> actor) continue;
            string gyaml = Text(actor, "Gyaml");
            var (x, y, z) = Vec(actor, "Translate");
            var (rx, ry, rz) = Vec(actor, "Rotate");
            var scale = Vec(actor, "Scale");
            string skip = SkipReason(gyaml);
            found.Add(new ActorPlacement
            {
                Gyaml = gyaml,
                Hash = Hash(actor),
                Name = Text(actor, "Name"),
                Source = source,
                X = x, Y = y, Z = z,
                Rx = rx, Ry = ry, Rz = rz,
                Sx = scale == default ? 1 : scale.X,
                Sy = scale == default ? 1 : scale.Y,
                Sz = scale == default ? 1 : scale.Z,
                Draw = skip.Length == 0 && gyaml.Length > 0,
                SkipReason = skip,
            });
        }
        return found;
    }

    public static string Category(string gyaml)
    {
        if (gyaml.Contains("AirWall", StringComparison.Ordinal) || gyaml.StartsWith("BGM", StringComparison.Ordinal) || gyaml.StartsWith("Bgm", StringComparison.Ordinal))
            return "system";
        if (gyaml.StartsWith("Npc_", StringComparison.Ordinal) || gyaml.StartsWith("Shop_", StringComparison.Ordinal)) return "npc";
        if (gyaml.StartsWith("Enemy_", StringComparison.Ordinal)) return "enemy";
        if (gyaml.StartsWith("Animal_", StringComparison.Ordinal)) return "animal";
        if (gyaml.StartsWith("Eft", StringComparison.Ordinal)) return "effect";
        if (gyaml.StartsWith("Land_", StringComparison.Ordinal)) return "land";
        if (gyaml.StartsWith("Area_", StringComparison.Ordinal) || gyaml.StartsWith("Area", StringComparison.Ordinal)) return "area";
        if (gyaml.StartsWith("Obj_", StringComparison.Ordinal) || gyaml.StartsWith("FldObj_", StringComparison.Ordinal)
            || gyaml.StartsWith("Item_", StringComparison.Ordinal) || gyaml.StartsWith("Weapon_", StringComparison.Ordinal)
            || gyaml.StartsWith("Dm_", StringComparison.Ordinal)) return "object";
        if (gyaml.StartsWith("StartPos", StringComparison.Ordinal) || gyaml.StartsWith("Player", StringComparison.Ordinal)
            || gyaml.StartsWith("Sound", StringComparison.Ordinal) || gyaml.StartsWith("AirWall", StringComparison.Ordinal)
            || gyaml.StartsWith("Ground", StringComparison.Ordinal)) return "system";
        if (gyaml.Contains("Anchor", StringComparison.Ordinal) || gyaml.EndsWith("Actor", StringComparison.Ordinal)) return "system";
        return "other";
    }

    public static string SkipReason(string gyaml)
    {
        string category = Category(gyaml);
        if (category is "system" or "area" or "effect") return $"{category} actor is not drawn";
        if (IsMarker(gyaml)) return "marker is not drawn";
        if (IsVolume(gyaml)) return "volume is not drawn";
        return "";
    }

    static bool IsMarker(string gyaml)
    {
        if (gyaml.Contains("airwall", StringComparison.OrdinalIgnoreCase)) return true;
        if (gyaml.Contains("Anchor", StringComparison.OrdinalIgnoreCase)) return true;
        if (gyaml.EndsWith("Actor", StringComparison.Ordinal)) return true;
        if (gyaml.StartsWith("EftLoc_", StringComparison.Ordinal) || gyaml.StartsWith("Loc_", StringComparison.Ordinal)
            || gyaml.StartsWith("Tag_", StringComparison.Ordinal) || gyaml.Contains("Locator", StringComparison.Ordinal))
            return true;
        return false;
    }

    /// <summary>
    /// Areas, fog, and the other region actors. A <c>Land_</c> mesh is not one of these
    /// unless its name is an air wall, which <see cref="IsMarker"/> already caught.
    /// </summary>
    static bool IsVolume(string gyaml)
    {
        if (gyaml.StartsWith("Land_", StringComparison.OrdinalIgnoreCase)) return false;
        string lower = gyaml.ToLowerInvariant();
        foreach (string word in new[] { "area", "volume", "fog", "trigger", "sensor", "collision", "collider" })
            if (lower.Contains(word, StringComparison.Ordinal)) return true;
        return false;
    }

    static string Text(Dictionary<string, object?> actor, string key) =>
        actor.GetValueOrDefault(key) as string ?? "";

    static ulong Hash(Dictionary<string, object?> actor) => actor.GetValueOrDefault("Hash") switch
    {
        BymlU64 value => value.Value,
        long value => unchecked((ulong)value),
        int value => unchecked((uint)value),
        _ => 0,
    };

    static (double X, double Y, double Z) Vec(Dictionary<string, object?> actor, string key)
    {
        if (!actor.TryGetValue(key, out var value) || value is null) return default;
        if (value is List<object?> list && list.Count >= 3)
            return (Num(list[0]), Num(list[1]), Num(list[2]));
        if (value is Dictionary<string, object?> dict)
            return (Num(dict.GetValueOrDefault("X")), Num(dict.GetValueOrDefault("Y")), Num(dict.GetValueOrDefault("Z")));
        return default;
    }

    static double Num(object? value) => value switch
    {
        double number => number,
        float number => number,
        int number => number,
        long number => number,
        BymlF64 number => number.Value,
        _ => 0,
    };
}
