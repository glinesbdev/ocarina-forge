namespace OcarinaForge.Import;

/// <summary>Resolves the AnimationRef in the actor pack and exact model and actor clip filenames.</summary>
public sealed class AnimationLocator(DumpFolder dump)
{
    readonly Dictionary<string, List<ImportedClip>> _clipsByFile = new(StringComparer.Ordinal);

    public void ClearDecoded() => _clipsByFile.Clear();

    public void Attach(string actor, ResolvedModel resolved, List<string> notes)
    {
        var model = resolved.Model;
        if (model.Skeleton.Count == 0) return;
        var boneNames = model.Skeleton.Select(bone => bone.Name).ToHashSet(StringComparer.Ordinal);
        var clipNames = model.Clips.Select(clip => clip.Name).ToHashSet(StringComparer.Ordinal);
        var clips = new List<ImportedClip>(model.Clips);
        bool childEpona = actor.Equals("Animal_Epona_Child", StringComparison.Ordinal);
        var projects = new List<string>();
        try { ReadAnimationRef(actor, projects, model.AnimationSources, allowRetargeted: !childEpona); }
        catch (ImportException exc) { notes.Add($"{actor} animation component: {exc.Message}"); }

        string modelFile = Path.GetFileName(resolved.Path.Split(':').Last()).Split('.')[0];
        string[] names = [model.Name, modelFile, actor];
        projects.AddRange(names);
        projects.AddRange(names.Where(name => name.Length > 0).Select(name => name + "_Animation"));
        if (childEpona)
        {
            // The melody sequence has a standalone clip file authored for the child rig.
            projects.Add("Dm_MelodyGet_Epona_Animal_Horse_Epona_Child");
            projects.RemoveAll(project => project.Contains("Epona_Adult", StringComparison.OrdinalIgnoreCase));
        }
        foreach (string project in projects.Distinct(StringComparer.Ordinal))
        {
            if (!ValidProject(project)) continue;
            string path = $"Model/{project}.anim.bfres.zs";
            if (!dump.Exists(path)) path = path[..^3];
            if (!dump.Exists(path)) continue;
            if (!_clipsByFile.TryGetValue(path, out var external))
            {
                try { external = FskaReader.ReadFile(dump.Read(path)); }
                catch (ImportException exc)
                {
                    notes.Add($"{path}: {exc.Message}");
                    external = new List<ImportedClip>();
                }
                _clipsByFile[path] = external;
            }
            model.AnimationSources.Add(path);
            foreach (var clip in external)
                if (clip.Bones.Any(track => boneNames.Contains(track.Bone)) && clipNames.Add(clip.Name))
                    clips.Add(clip);
        }
        model.Clips = clips;
        SkinBinder.Apply(model);
    }

    void ReadAnimationRef(string actor, List<string> projects, List<string> sources, bool allowRetargeted)
    {
        var visited = new HashSet<string>(StringComparer.Ordinal);
        while (ValidProject(actor) && visited.Add(actor) && visited.Count <= 32)
        {
            string pack = $"Pack/Actor/{actor}.pack.zs";
            if (!dump.Exists(pack)) pack = pack[..^3];
            if (!dump.Exists(pack)) return;
            var archive = new SarcArchive(dump.Read(pack));
            string actorPath = $"Actor/{actor}.engine__actor__ActorParam.bgyml";
            if (!archive.Names.Contains(actorPath)) return;
            if (ReadMember(archive, actorPath) is not Dictionary<string, object?> actorParam) return;
            sources.Add($"{pack}#{actorPath}");
            if (actorParam.GetValueOrDefault("Components") is Dictionary<string, object?> components
                && components.TryGetValue("AnimationRef", out var reference))
            {
                if (reference is not string path) return;
                path = path.TrimStart('?');
                if (path.StartsWith("Work/", StringComparison.Ordinal)) path = path[5..];
                if (path.EndsWith(".gyml", StringComparison.Ordinal)) path = path[..^5] + ".bgyml";
                if (!path.StartsWith("Component/AnimationParam/", StringComparison.Ordinal) || path.Contains("..", StringComparison.Ordinal))
                    return;
                if (archive.Names.Contains(path))
                {
                    sources.Add($"{pack}#{path}");
                    AddProjects(ReadMember(archive, path), projects, allowRetargeted);
                }
                else if (dump.Exists(path))
                {
                    sources.Add(path);
                    AddProjects(dump.ReadByml(path), projects, allowRetargeted);
                }
                return;
            }
            if (actorParam.GetValueOrDefault("$parent") is not string parent) return;
            const string suffix = ".engine__actor__ActorParam.gyml";
            if (!parent.StartsWith("Work/Actor/", StringComparison.Ordinal) || !parent.EndsWith(suffix, StringComparison.Ordinal))
                return;
            actor = parent["Work/Actor/".Length..^suffix.Length];
        }
    }

    object? ReadMember(SarcArchive archive, string member) =>
        Byml.Load(ZstdFrames.Decompress(archive.Read(member), dump.Dictionaries));

    static void AddProjects(object? component, List<string> projects, bool allowRetargeted)
    {
        if (component is not Dictionary<string, object?> document
            || document.GetValueOrDefault("AnimationResources") is not List<object?> resources) return;
        foreach (var resource in resources)
            if (resource is Dictionary<string, object?> entry
                && (allowRetargeted || entry.GetValueOrDefault("IsRetarget") is not true)
                && entry.GetValueOrDefault("ModelProjectName") is string project)
                projects.Add(project);
    }

    static bool ValidProject(string name) => name.Length > 0
        && name.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or '-');
}
