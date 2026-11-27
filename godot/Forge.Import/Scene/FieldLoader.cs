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
    public LoadReport Report { get; set; } = new();
    /// <summary>MaterialAlb layers in shader-index order, still ASTC until the viewer transcodes them.</summary>
    public List<CompressedTexture> TerrainAlbedo { get; set; } = new();
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
                field.Report.ActorsSkipped++;
                field.Actors.Add(new FieldActor { Placement = placement, Note = placement.SkipReason });
                continue;
            }
            if (!cache.TryGetValue(placement.Gyaml, out var resolved))
            {
                var found = ModelLocator.Find(dump, placement.Gyaml, field.Notes);
                if (found is null)
                {
                    field.Report.Miss(placement.Gyaml);
                    field.Actors.Add(new FieldActor { Placement = placement, Note = $"no model for {placement.Gyaml}" });
                    continue;
                }
                foreach (string note in found.Model.Notes)
                {
                    field.Report.ShapesFailed++;
                    field.Report.AddReason(note);
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
            field.Report.ActorsLoaded++;
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
        foreach (string note in field.Notes)
            field.Report.AddReason(note);
        return field;
    }

    static void TryTerrain(DumpFolder dump, string banc, List<ActorPlacement> placements, ImportedField field)
    {
        mateTiles = 0;
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
                int[]? mate = null;
                foreach (string matePath in new[]
                {
                    $"{folder}/{sceneDir}/{tile.Name}.mate.zs",
                    $"{folder}/{sceneDir}/{tile.Name}.mate",
                    $"TerrainArc/{sceneDir}/{tile.Name}.mate.zs",
                    $"TerrainArc/{sceneDir}/{tile.Name}.mate",
                })
                {
                    if (!dump.Exists(matePath)) continue;
                    try { mate = TerrainMesh.ReadMate(dump.Read(matePath), dump.Dictionaries); }
                    catch (ImportException) { mate = null; }
                    break;
                }
                if (mate is not null) mateTiles++;
                built.Add(new TerrainMesh.Tile
                {
                    Name = tile.Name,
                    CenterX = tile.X,
                    CenterZ = tile.Z,
                    Edge = tile.Edge,
                    Heights = TerrainMesh.DecodeHeights(payload),
                    Layers = mate,
                });
            }
            catch (ImportException exc)
            {
                field.Notes.Add($"{tile.Name}: {exc.Message}");
            }
        }
        if (built.Count == 0) return;
        field.Terrain = TerrainMesh.Build(built, scale, heightScale, origin, clip, steps);
        if (mateTiles == 0)
            field.Report.AddReason("terrain has no material grid; albedo stays the flat colour");
        else
            AttachTerrainAlbedo(dump, field, mateTiles, built.Count);
    }

    static int mateTiles;

    static void AttachTerrainAlbedo(DumpFolder dump, ImportedField field, int withGrid, int tiles)
    {
        var part = field.Terrain;
        if (part?.MaterialLayers is null)
        {
            field.Report.AddReason($"terrain mate: {withGrid} of {tiles} tiles decoded, none of the drawn samples had a layer");
            return;
        }
        var used = new List<int>();
        foreach (int layer in part.MaterialLayers)
            if (layer >= 0 && !used.Contains(layer)) used.Add(layer);
        string path = dump.Exists("Tex/MaterialAlb.bntx.zs") ? "Tex/MaterialAlb.bntx.zs"
            : dump.Exists("Tex/MaterialAlb.bntx") ? "Tex/MaterialAlb.bntx" : "";
        if (path.Length == 0)
        {
            field.Report.AddReason("Tex/MaterialAlb.bntx is not in this dump");
            return;
        }
        List<CompressedTexture?> layers;
        try { layers = BntxReader.ReadLayers(dump.Read(path)); }
        catch (ImportException exc)
        {
            field.Report.AddReason($"MaterialAlb: {exc.Message}");
            return;
        }
        var chosen = new List<CompressedTexture>();
        var map = new Dictionary<int, int>();
        foreach (int index in used)
        {
            CompressedTexture? image = index >= 0 && index < layers.Count ? layers[index] : null;
            if (image is null)
            {
                map[index] = -1;
                continue;
            }
            map[index] = chosen.Count;
            chosen.Add(image);
        }
        for (int i = 0; i < part.MaterialLayers.Count; i++)
        {
            int layer = part.MaterialLayers[i];
            part.MaterialLayers[i] = layer >= 0 && map.TryGetValue(layer, out int compact) ? compact : -1;
        }
        field.TerrainAlbedo = chosen;
        if (chosen.Count == 0)
            field.Report.AddReason("MaterialAlb had none of the layers the material grid asked for");
        else
            field.Report.AddReason($"terrain albedo: {chosen.Count} MaterialAlb layers, ASTC until transcode ({withGrid} of {tiles} tiles had a grid)");
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
