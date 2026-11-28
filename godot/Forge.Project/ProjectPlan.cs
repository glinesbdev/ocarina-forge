namespace OcarinaForge.Project;

public sealed class PlannedOutput
{
    public string ResPath { get; init; } = "";
    public string Kind { get; init; } = "";
}

public sealed class TextureJob
{
    public string Name { get; init; } = "";
    public string ResPath { get; init; } = "";
    public string Source { get; init; } = "";
    public string Hash { get; init; } = "";
    public bool Current { get; set; }
    public CompressedTexture Image { get; init; } = new();
}

public sealed class ModelJob
{
    public string Gyaml { get; init; } = "";
    public string ModelPath { get; init; } = "";
    public string Hash { get; init; } = "";
    public List<string> Sources { get; init; } = new();
    public List<PlannedOutput> Outputs { get; init; } = new();
    public Dictionary<string, string> TexturePaths { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public Dictionary<string, string> MaterialPaths { get; init; } = new(StringComparer.Ordinal);
    public bool Current { get; set; }
    public ImportedModel Model { get; init; } = new();
    public Dictionary<string, CompressedTexture> Textures { get; init; } = new(StringComparer.OrdinalIgnoreCase);
    public List<FieldActor> Actors { get; init; } = new();
}

public sealed class MapJob
{
    public string Hash { get; init; } = "";
    public List<string> Sources { get; init; } = new();
    public List<PlannedOutput> Outputs { get; init; } = new();
    public bool Current { get; set; }
    public bool HasTerrain { get; set; }
    public bool HasWater { get; set; }
}

public sealed class ProjectJob
{
    public string Dump { get; init; } = "";
    public string Banc { get; init; } = "";
    public List<TextureJob> Textures { get; init; } = new();
    public List<ModelJob> Models { get; init; } = new();
    public MapJob Map { get; init; } = new();
    public ImportedField Field { get; init; } = new();
}

public static class ProjectPlan
{
    public const string ImporterVersion = "godot-project-1";

    public static ProjectJob Build(DumpFolder dump, ImportedField field, ManifestDocument? existing, string projectDir)
    {
        var textureOwners = new Dictionary<string, string>(StringComparer.Ordinal);
        var textures = new Dictionary<string, TextureJob>(StringComparer.Ordinal);
        var models = new List<ModelJob>();
        bool water = false;
        foreach (var group in field.Actors.Where(actor => actor.Model is not null).GroupBy(actor => actor.Placement.Gyaml, StringComparer.Ordinal))
        {
            var actors = group.ToList();
            var model = actors[0].Model!;
            var sources = new List<string> { actors[0].ModelPath };
            sources.AddRange(new TextureLibrary(dump).CompanionPaths(actors[0].ModelPath, group.Key));
            foreach (var (name, source) in actors[0].TextureSources)
                if (source.Length > 0) sources.Add(source);
            string hash = SourceHash.Of(dump, sources);
            var outputs = new List<PlannedOutput>
            {
                new() { ResPath = ProjectPaths.ModelScene(group.Key), Kind = "model" },
            };
            var texturePaths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var (name, image) in actors[0].Textures)
            {
                string source = actors[0].TextureSources.TryGetValue(name, out var found) && found.Length > 0 ? found : actors[0].ModelPath;
                string path = ClaimTexture(dump, textureOwners, name, source);
                texturePaths[name] = path;
                if (!textures.ContainsKey(path))
                {
                    string textureHash = SourceHash.Of(dump, new[] { source });
                    textures[path] = new TextureJob
                    {
                        Name = name,
                        ResPath = path,
                        Source = source,
                        Hash = textureHash,
                        Current = ManifestStore.Current(existing, path, textureHash, projectDir),
                        Image = image,
                    };
                }
            }
            var materialPaths = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var part in model.Parts.Where(ProjectPaths.Drawn))
            {
                outputs.Add(new PlannedOutput { ResPath = ProjectPaths.Mesh(group.Key, part.Name, part.Lod), Kind = "mesh" });
                if (part.Water)
                {
                    water = true;
                    continue;
                }
                string key = MaterialKey(part);
                if (materialPaths.ContainsKey(key)) continue;
                string materialPath = ProjectPaths.Material(group.Key, key);
                materialPaths[key] = materialPath;
                outputs.Add(new PlannedOutput { ResPath = materialPath, Kind = "material" });
            }
            bool current = outputs.All(output => ManifestStore.Current(existing, output.ResPath, hash, projectDir));
            models.Add(new ModelJob
            {
                Gyaml = group.Key,
                ModelPath = actors[0].ModelPath,
                Hash = hash,
                Sources = sources.Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal).ToList(),
                Outputs = outputs,
                TexturePaths = texturePaths,
                MaterialPaths = materialPaths,
                Current = current,
                Model = model,
                Textures = actors[0].Textures,
                Actors = actors,
            });
        }

        var mapSources = new List<string> { field.Banc };
        string dynamic = field.Banc.Replace("_Static", "_Dynamic", StringComparison.Ordinal);
        if (!dynamic.Equals(field.Banc, StringComparison.Ordinal) && dump.Exists(dynamic)) mapSources.Add(dynamic);
        mapSources.AddRange(field.TerrainSources);
        foreach (var model in models) mapSources.AddRange(model.Sources);
        var mapOutputs = new List<PlannedOutput>
        {
            new() { ResPath = ProjectPaths.MapScene, Kind = "map" },
        };
        bool hasTerrain = field.Terrain is { Positions.Count: > 0, Indices.Count: >= 3 };
        if (hasTerrain)
        {
            mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.TerrainShader, Kind = "shader" });
            mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.TerrainMesh, Kind = "mesh" });
            mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.TerrainMaterial, Kind = "material" });
            mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.TerrainOccluder, Kind = "occluder" });
            if (field.TerrainAlbedo.Count > 0)
            {
                mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.TerrainArray, Kind = "texture" });
                for (int i = 0; i < field.TerrainAlbedo.Count; i++)
                    mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.TerrainLayer(i), Kind = "texture" });
            }
        }
        if (water) mapOutputs.Add(new PlannedOutput { ResPath = ProjectPaths.WaterMaterial, Kind = "material" });
        string mapHash = SourceHash.Of(dump, mapSources);
        return new ProjectJob
        {
            Dump = dump.Root,
            Banc = field.Banc,
            Textures = textures.Values.OrderBy(item => item.ResPath, StringComparer.Ordinal).ToList(),
            Models = models,
            Field = field,
            Map = new MapJob
            {
                Hash = mapHash,
                Sources = mapSources.Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal).ToList(),
                Outputs = mapOutputs,
                Current = mapOutputs.All(output => ManifestStore.Current(existing, output.ResPath, mapHash, projectDir)),
                HasTerrain = hasTerrain,
                HasWater = water,
            },
        };
    }

    public static ManifestDocument ToManifest(ProjectJob job)
    {
        var document = new ManifestDocument
        {
            Dump = job.Dump,
            Banc = job.Banc,
            Summary = new ManifestSummary
            {
                GeometryBytes = job.Field.GeometryBytes,
                Bc7Bytes = job.Field.Bc7Bytes,
                ActorsLoaded = job.Field.Report.ActorsLoaded,
                ActorsMissing = job.Field.Report.ActorsMissing,
                ActorsSkipped = job.Field.Report.ActorsSkipped,
                TexturesOk = job.Field.Report.TexturesOk,
                TexturesFailed = job.Field.Report.TexturesFailed,
                TexturesMissing = job.Field.Report.TexturesMissing,
                MaterialsUnbound = job.Field.Report.MaterialsUnbound,
                ShapesFailed = job.Field.Report.ShapesFailed,
            },
        };
        void Add(IEnumerable<PlannedOutput> outputs, string hash, List<string> sources)
        {
            foreach (var output in outputs)
            {
                document.Files[output.ResPath] = new ManifestEntry
                {
                    Kind = output.Kind,
                    Sources = sources,
                    Hash = hash,
                };
            }
        }
        foreach (var texture in job.Textures)
        {
            document.Files[texture.ResPath] = new ManifestEntry
            {
                Kind = "texture",
                Sources = new List<string> { texture.Source },
                Hash = texture.Hash,
            };
        }
        foreach (var model in job.Models) Add(model.Outputs, model.Hash, model.Sources);
        Add(job.Map.Outputs, job.Map.Hash, job.Map.Sources);
        return document;
    }

    static string ClaimTexture(DumpFolder dump, Dictionary<string, string> owners, string name, string source)
    {
        string path = ProjectPaths.Texture(name);
        if (!owners.TryGetValue(path, out var owner) || owner == source)
        {
            owners[path] = source;
            return path;
        }
        string suffix = SourceHash.Of(dump, new[] { source })[..8];
        path = $"res://textures/{ProjectPaths.Safe(name)}_{suffix}.res";
        owners[path] = source;
        return path;
    }

    public static string MaterialKey(ImportedPart part)
    {
        if (part.Material.Length > 0) return part.Material;
        if (part.Texture.Length > 0) return part.Texture;
        return part.Name.Length > 0 ? part.Name : "material";
    }
}
