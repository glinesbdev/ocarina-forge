using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Godot;
using OcarinaForge.Import;
using OcarinaForge.Project;

namespace OcarinaForge.Generated;

public static partial class ProjectEmitter
{
    sealed record CatalogEntry(string Source, string Model, string? Scene, string Status, List<string> Notes);

    static int GenerateCatalog(string projectDir, ProjectRequest request, DumpFolder dump)
    {
        string reportDirectory = Path.Combine(projectDir, ".ocarina");
        using var catalogLock = new FileStream(Path.Combine(reportDirectory, "catalog.lock"),
            FileMode.OpenOrCreate, System.IO.FileAccess.Write, FileShare.None);
        var discoveryNotes = new List<string>();
        var sources = ModelCatalog.Discover(dump, request.ModelPrefix, discoveryNotes).ToList();
        GD.Print($"catalog discovered {sources.Count} model containers");
        var entries = new List<CatalogEntry>();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var textureOwners = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var manifest = ManifestStore.Load(projectDir);
        if (manifest?.Importer != ProjectPlan.ImporterVersion) manifest = new ManifestDocument();
        if (manifest.Dump.Length > 0 && !manifest.Dump.Equals(dump.Root, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("This catalog belongs to a different dump. Choose a new output folder.");
        manifest.Dump = dump.Root;
        manifest.Banc = "";
        manifest.Summary = new ManifestSummary();
        var textures = new TextureLibrary(dump);
        var animations = new AnimationLocator(dump);
        bool waterWritten = false;
        for (int index = 0; index < sources.Count; index++)
        {
            var source = sources[index];
            List<ImportedModel> models;
            try { models = BfresReader.Read(source.Read(dump)); }
            catch (Exception exc) when (exc is ImportException or IOException)
            {
                entries.Add(new CatalogEntry(source.Path, "", null, "unsupported", [exc.Message]));
                continue;
            }
            for (int member = 0; member < models.Count; member++)
            {
                var model = models[member];
                var notes = new List<string>(model.Notes);
                if (!model.Parts.Any(ProjectPaths.Drawn))
                {
                    entries.Add(new CatalogEntry(source.Path, model.Name, null, "no mesh", notes));
                    continue;
                }
                string actor = source.Actor ?? model.Name;
                string name = CatalogName(names, models.Count == 1 ? actor : model.Name, source.Path, member);
                string scene = ProjectPaths.ModelScene(name);
                try
                {
                    animations.Attach(actor, new ResolvedModel { Model = model, Path = source.Path }, notes);
                    if (source.ActorPack is not null) model.AnimationSources.Add(source.ActorPack);
                    var field = new ImportedField();
                    var (images, refs) = FieldLoader.ReadTextures(textures, model, source.Path, actor, field.Report);
                    field.Actors.Add(new FieldActor { Model = model, ModelPath = source.Path,
                        Placement = new ActorPlacement { Gyaml = name }, Textures = images, TextureSources = refs });
                    var job = ProjectPlan.Build(dump, field, manifest, projectDir, textureOwners, modelsOnly: true);
                    var modelJob = job.Models[0];
                    if (job.Map.HasWater && !waterWritten)
                    {
                        string waterPath = ProjectPaths.WaterMaterial;
                        string waterHash = SourceHash.Of(dump, []);
                        if (!ManifestStore.Current(manifest, waterPath, waterHash, projectDir)) WriteWater();
                        manifest.Files[waterPath] = new ManifestEntry { Kind = "material", Hash = waterHash };
                        waterWritten = true;
                    }
                    var missingTextures = new HashSet<string>(StringComparer.Ordinal);
                    foreach (var texture in job.Textures.Where(item => !item.Current))
                    {
                        var image = texture.Image.Codec == GpuCodec.Astc
                            ? AstcTranscode.Transcode(texture.Image, field.Report) : texture.Image;
                        if (image is null || !SaveTexture(image, texture.ResPath, field.Report))
                            missingTextures.Add(texture.ResPath);
                    }
                    foreach (var (key, path) in modelJob.TexturePaths.ToList())
                        if (missingTextures.Contains(path)) modelJob.TexturePaths.Remove(key);
                    bool current = modelJob.Current && job.Textures.All(item => item.Current);
                    if (!current) WriteModel(modelJob);
                    foreach (var (path, entry) in ProjectPlan.ToManifest(job).Files)
                        if (!missingTextures.Contains(path) && (missingTextures.Count == 0 || entry.Kind == "texture"))
                            manifest.Files[path] = entry;
                    if (missingTextures.Count > 0)
                        foreach (var output in modelJob.Outputs) manifest.Files.Remove(output.ResPath);
                    manifest.Summary.ActorsLoaded++;
                    manifest.Summary.TexturesMissing += field.Report.TexturesMissing;
                    notes.AddRange(field.Report.MissingTextures.Keys.Select(key => $"missing texture {key}"));
                    notes.AddRange(field.Report.Reasons.Keys);
                    entries.Add(new CatalogEntry(source.Path, model.Name, scene,
                        missingTextures.Count > 0 ? "partial" : current ? "unchanged" : "written", notes));
                }
                catch (Exception exc) when (exc is ImportException or InvalidOperationException or ArgumentException)
                {
                    notes.Add(exc.Message);
                    entries.Add(new CatalogEntry(source.Path, model.Name, null, "failed", notes));
                }
            }
            if ((index + 1) % 32 == 0)
            {
                textures.ClearDecoded();
                animations.ClearDecoded();
            }
            if ((index + 1) % 128 == 0)
            {
                ManifestStore.Save(projectDir, manifest);
                GD.Print($"catalog processed {index + 1}/{sources.Count} containers");
            }
        }
        manifest.Summary.ActorsMissing = entries.Count(entry => entry.Status is "unsupported" or "failed");
        manifest.Summary.ActorsSkipped = entries.Count(entry => entry.Status == "no mesh");
        manifest.Summary.TexturesOk = manifest.Files.Values.Count(entry => entry.Kind == "texture");
        ManifestStore.Save(projectDir, manifest);
        string report = Path.Combine(reportDirectory, "model-catalog.json");
        File.WriteAllText(report, JsonSerializer.Serialize(new { sources = sources.Count, discoveryNotes, models = entries },
            new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }) + "\n");
        GD.Print($"catalog models {manifest.Summary.ActorsLoaded}, failed or unsupported {manifest.Summary.ActorsMissing}, report {report}");
        return manifest.Summary.ActorsLoaded > 0 ? 0 : 1;
    }

    static string CatalogName(HashSet<string> names, string preferred, string source, int member)
    {
        string name = ProjectPaths.Safe(preferred);
        if (names.Add(name)) return name;
        string suffix = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes($"{source}#{member}")))[..10];
        name = $"{name[..Math.Min(name.Length, 69)]}_{suffix}";
        if (!names.Add(name)) throw new InvalidOperationException($"duplicate catalog scene {name}");
        return name;
    }

    static int CheckCatalog(string projectDir)
    {
        var manifest = ManifestStore.Load(projectDir);
        var paths = manifest?.Files.Where(file => file.Value.Kind == "model").Select(file => file.Key).ToArray() ?? [];
        if (paths.Length == 0 || paths.Any(path => !File.Exists(ProjectPaths.Disk(projectDir, path))))
        {
            GD.PrintErr("check catalog scene missing");
            return 1;
        }
        using var sample = ResourceLoader.Load<PackedScene>(paths[0]);
        var model = sample?.Instantiate<Node3D>();
        if (model is null) return 1;
        int meshes = Count<MeshInstance3D>(model);
        model.Free();
        GD.Print($"check catalog {paths.Length} scenes, sample {meshes} meshes");
        return meshes > 0 ? 0 : 1;
    }
}
