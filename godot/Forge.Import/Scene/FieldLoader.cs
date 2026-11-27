namespace OcarinaForge.Import;

public sealed class FieldActor
{
    public ActorPlacement Placement { get; init; } = new();
    public string ModelPath { get; init; } = "";
    public ImportedModel? Model { get; init; }
    public Dictionary<string, CompressedTexture> Textures { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public string Note { get; init; } = "";
}

public sealed class ImportedField
{
    public string Banc { get; init; } = "";
    public string Scene { get; set; } = "";
    public List<FieldActor> Actors { get; init; } = new();
    public ImportedPart? Terrain { get; set; }
    public List<string> Notes { get; init; } = new();
    public long GeometryBytes { get; set; }
    public long Bc7Bytes { get; set; }
    public int Drawables { get; set; }
}

public static class FieldLoader
{
    public const string DefaultBanc = "Banc/HyruleField/HyruleField_Child_Static.bcett.byml.zs";

    public static ImportedField Load(DumpFolder dump, string? banc = null)
    {
        string relative = string.IsNullOrWhiteSpace(banc) ? DefaultBanc : banc.Replace('\\', '/');
        if (!dump.Exists(relative))
        {
            string bare = relative.EndsWith(".zs", StringComparison.Ordinal) ? relative[..^3] : relative + ".zs";
            if (dump.Exists(bare)) relative = bare;
        }
        if (!dump.Exists(relative))
            throw new ImportException($"placement file is not in this dump: {relative}");
        var field = new ImportedField { Banc = relative };
        var placements = ActorList.Read(dump.ReadByml(relative), "static");
        string dynamic = relative.Replace("_Static", "_Dynamic", StringComparison.Ordinal);
        if (!dynamic.Equals(relative, StringComparison.Ordinal) && dump.Exists(dynamic))
            placements.AddRange(ActorList.Read(dump.ReadByml(dynamic), "dynamic"));
        var cache = new Dictionary<string, (string Path, ImportedModel Model)>(StringComparer.Ordinal);
        foreach (var placement in placements)
        {
            if (!placement.Draw)
            {
                field.Actors.Add(new FieldActor { Placement = placement, Note = placement.SkipReason });
                continue;
            }
            if (!cache.TryGetValue(placement.Gyaml, out var resolved))
            {
                var found = ModelLocator.Find(dump, placement.Gyaml, field.Notes);
                if (found is null)
                {
                    field.Actors.Add(new FieldActor { Placement = placement, Note = $"no model for {placement.Gyaml}" });
                    field.Notes.Add($"no model for {placement.Gyaml}");
                    continue;
                }
                resolved = (found.Path, found.Model);
                cache[placement.Gyaml] = resolved;
            }
            var textures = new Dictionary<string, CompressedTexture>(StringComparer.OrdinalIgnoreCase);
            foreach (var part in resolved.Model.Parts)
            {
                if (part.OmitReason.Length > 0 || part.Helper.Length > 0 || part.Lod != 0 && part.Lod != 1) continue;
                if (part.Texture.Length == 0) continue;
                if (textures.ContainsKey(part.Texture)) continue;
                var image = ModelLocator.FindTexture(dump, resolved.Model, part.Texture, $"Pack/Actor/{placement.Gyaml}.pack.zs");
                if (image is not null) textures[part.Texture] = image;
            }
            field.Actors.Add(new FieldActor
            {
                Placement = placement,
                ModelPath = resolved.Path,
                Model = resolved.Model,
                Textures = textures,
            });
        }
        TryTerrain(dump, relative, placements, field);
        long geom = 0;
        long bc7 = 0;
        int drawables = 0;
        var seenTex = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var actor in field.Actors)
        {
            if (actor.Model is null) continue;
            drawables++;
            foreach (var part in actor.Model.Parts)
            {
                if (part.Lod > 1 || part.OmitReason.Length > 0) continue;
                geom += part.Positions.Count * 12L + part.Indices.Count * 4L;
            }
            foreach (var (name, image) in actor.Textures)
            {
                if (!seenTex.Add(actor.ModelPath + "|" + name)) continue;
                if (image.Codec == GpuCodec.Bc7) bc7 += image.Blocks.Length;
            }
        }
        if (field.Terrain is not null)
            geom += field.Terrain.Positions.Count * 12L + field.Terrain.Indices.Count * 4L;
        field.GeometryBytes = geom;
        field.Bc7Bytes = bc7;
        field.Drawables = drawables;
        return field;
    }

    static void TryTerrain(DumpFolder dump, string banc, List<ActorPlacement> placements, ImportedField field)
    {
        Dictionary<string, object?>? document = null;
        foreach (string stem in TerrainMesh.LevelStems(banc))
        {
            foreach (string relative in ParamCandidates(stem))
            {
                if (!dump.Exists(relative)) continue;
                document = ReadParam(dump, relative, field.Notes, new HashSet<string>(), 0);
                break;
            }
            if (document is not null) break;
        }
        string scene = document?.GetValueOrDefault("TerrainSceneName") as string ?? "";
        var origin = Origin(document);
        var scenes = new List<string>();
        foreach (string text in new[] { scene }.Concat(TerrainMesh.LevelStems(banc)))
            if (text.Length > 0 && !scenes.Contains(text)) scenes.Add(text);
        string tscb = "";
        foreach (string item in scenes)
        {
            foreach (string relative in TscbCandidates(item))
            {
                if (dump.Exists(relative)) { tscb = relative; break; }
            }
            if (tscb.Length > 0) break;
        }
        if (tscb.Length == 0)
        {
            if (scene.Length > 0) field.Notes.Add($"GameBancParam names terrain scene {scene}, but no TSCB was found");
            return;
        }
        field.Scene = scene.Length > 0 ? scene : Path.GetFileNameWithoutExtension(tscb).Replace(".tscb", "");
        var (scale, heightScale, tiles) = TerrainMesh.ReadTscb(dump.Read(tscb));
        string folder = Path.GetDirectoryName(tscb.Replace('\\', '/'))?.Replace('\\', '/') ?? "TerrainArc";
        string sceneDir = field.Scene;
        var clip = TerrainMesh.ActorClip(placements.Select(item => (item.X, item.Z)));
        var focus = clip is { } window ? ((window.X0 + window.X1) / 2, (window.Z0 + window.Z1) / 2) : (origin.X, origin.Z);
        var steps = TerrainMesh.SampleSteps(tiles, scale, origin, focus);
        var built = new List<TerrainMesh.Tile>();
        foreach (var tile in tiles)
        {
            string path = HeightPath(dump, folder, sceneDir, tile.Name);
            if (path.Length == 0) continue;
            try
            {
                var payload = TerrainMesh.UnpackHght(dump.Read(path), dump.Dictionaries);
                built.Add(new TerrainMesh.Tile
                {
                    Name = tile.Name,
                    CenterX = tile.X,
                    CenterZ = tile.Z,
                    Edge = tile.Edge,
                    Heights = TerrainMesh.DecodeHeights(payload),
                });
            }
            catch (ImportException exc)
            {
                field.Notes.Add($"{tile.Name}: {exc.Message}");
            }
        }
        if (built.Count == 0) return;
        field.Terrain = TerrainMesh.Build(built, scale, heightScale, origin, clip, steps);
        field.Notes.Add("terrain albedo is a flat colour; MaterialAlb ASTC is not uploaded");
    }

    static string HeightPath(DumpFolder dump, string folder, string scene, string name)
    {
        foreach (string relative in new[]
        {
            $"{folder}/{scene}/{name}.hght.zs",
            $"{folder}/{scene}/{name}.hght",
            $"TerrainArc/{scene}/{name}.hght.zs",
            $"TerrainArc/{scene}/{name}.hght",
        })
        {
            if (dump.Exists(relative)) return relative;
        }
        return "";
    }

    static Dictionary<string, object?>? ReadParam(DumpFolder dump, string relative, List<string> notes, HashSet<string> seen, int depth)
    {
        if (depth > 8 || !seen.Add(relative) || !dump.Exists(relative)) return null;
        if (dump.ReadByml(relative) is not Dictionary<string, object?> document) return null;
        if (document.GetValueOrDefault("$parent") is string parent && parent.Trim().Length > 0)
        {
            foreach (string path in ParentPaths(parent))
            {
                var loaded = ReadParam(dump, path, notes, seen, depth + 1);
                if (loaded is null) continue;
                document = Merge(loaded, document);
                break;
            }
        }
        return document;
    }

    static Dictionary<string, object?> Merge(Dictionary<string, object?> parent, Dictionary<string, object?> child)
    {
        var merged = new Dictionary<string, object?>(parent);
        foreach (var (key, value) in child)
        {
            if (key == "$parent") continue;
            if (value is Dictionary<string, object?> childDict && merged.GetValueOrDefault(key) is Dictionary<string, object?> parentDict)
                merged[key] = Merge(parentDict, childDict);
            else merged[key] = value;
        }
        return merged;
    }

    static List<string> ParentPaths(string text)
    {
        string path = text.Trim().Replace('\\', '/');
        if (path.StartsWith('?')) path = path[1..];
        if (path.StartsWith("Work/", StringComparison.Ordinal)) path = path[5..];
        path = path.TrimStart('/');
        if (path.EndsWith(".gyml", StringComparison.Ordinal)) path = path[..^5] + ".bgyml";
        var found = new List<string> { path };
        if (!path.EndsWith(".zs", StringComparison.Ordinal)) found.Add(path + ".zs");
        return found;
    }

    static (double X, double Y, double Z) Origin(Dictionary<string, object?>? document)
    {
        if (document?.GetValueOrDefault("TerrainScenePosition") is not Dictionary<string, object?> value)
            return (0, 0, 0);
        static double Num(object? item) => item is double number ? number : item is float numberf ? numberf : item is int numberi ? numberi : 0;
        return (Num(value.GetValueOrDefault("X")), Num(value.GetValueOrDefault("Y")), Num(value.GetValueOrDefault("Z")));
    }

    static IEnumerable<string> ParamCandidates(string stem)
    {
        string basis = $"Banc/GameBancParam/{stem}.game__banc__GameBancParam";
        yield return basis + ".bgyml";
        yield return basis + ".bgyml.zs";
        yield return basis + ".byml";
        yield return basis + ".byml.zs";
    }

    static IEnumerable<string> TscbCandidates(string scene)
    {
        string text = scene.Trim().Replace('\\', '/').TrimStart('/');
        if (text.StartsWith("Work/", StringComparison.Ordinal)) text = text[5..];
        if (text.EndsWith(".tscb.zs", StringComparison.Ordinal)) { yield return text; yield break; }
        if (text.EndsWith(".tscb", StringComparison.Ordinal)) { yield return text; yield return text + ".zs"; yield break; }
        yield return $"TerrainArc/{text}.tscb";
        yield return $"TerrainArc/{text}.tscb.zs";
        yield return $"Terrain/{text}.tscb";
        yield return $"Terrain/{text}.tscb.zs";
    }
}
