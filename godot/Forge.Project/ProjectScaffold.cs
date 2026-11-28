namespace OcarinaForge.Project;

public static class ProjectScaffold
{
    public static string FindTemplate(string? given)
    {
        if (!string.IsNullOrWhiteSpace(given))
        {
            string full = Path.GetFullPath(given);
            if (File.Exists(Path.Combine(full, "project.godot"))) return full;
            throw new InvalidOperationException($"template has no project.godot: {full}");
        }
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            string candidate = Path.Combine(dir.FullName, "godot", "template", "project.godot");
            if (File.Exists(candidate)) return Path.Combine(dir.FullName, "godot", "template");
            dir = dir.Parent;
        }
        throw new InvalidOperationException("godot/template was not found. Pass --template.");
    }

    /// <summary>Copies the editor shell and refreshes the addon. Leaves models, maps, and textures in place.</summary>
    public static void Write(string templateDir, string projectDir, string dump, string banc,
        bool modelsOnly = false, string modelPrefix = "")
    {
        Directory.CreateDirectory(projectDir);
        foreach (string source in Directory.EnumerateFiles(templateDir, "*", SearchOption.AllDirectories))
        {
            string relative = Path.GetRelativePath(templateDir, source);
            if (Skip(relative)) continue;
            string destName = relative.Replace('\\', '/').Equals("gitignore.txt", StringComparison.Ordinal) ? ".gitignore" : relative;
            string dest = Path.Combine(projectDir, destName);
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.Copy(source, dest, true);
        }
        CopyLibraries(projectDir);
        ManifestStore.SaveRequest(projectDir, new ProjectRequest { Dump = Path.GetFullPath(dump), Banc = banc,
            ModelsOnly = modelsOnly, ModelPrefix = modelPrefix });
    }

    public static void CopyLibraries(string projectDir)
    {
        string dest = Path.Combine(projectDir, "addons", "ocarina_forge", "lib");
        Directory.CreateDirectory(dest);
        Copy(typeof(DumpFolder).Assembly.Location, dest);
        Copy(typeof(ProjectPlan).Assembly.Location, dest);
        string? folder = Path.GetDirectoryName(typeof(DumpFolder).Assembly.Location);
        if (folder is not null)
        {
            string zstd = Path.Combine(folder, "ZstdSharp.dll");
            if (File.Exists(zstd)) Copy(zstd, dest);
            Copy(Path.Combine(folder, "AssetRipper.TextureDecoder.dll"), dest);
        }
    }

    static void Copy(string source, string destDir)
    {
        if (string.IsNullOrEmpty(source) || !File.Exists(source))
            throw new InvalidOperationException($"library is missing: {source}");
        File.Copy(source, Path.Combine(destDir, Path.GetFileName(source)), true);
    }

    static bool Skip(string relative)
    {
        string path = relative.Replace('\\', '/');
        if (path.StartsWith(".godot/", StringComparison.Ordinal) || path.Contains("/.godot/", StringComparison.Ordinal)) return true;
        if (path.StartsWith("bin/", StringComparison.Ordinal) || path.Contains("/bin/", StringComparison.Ordinal)) return true;
        if (path.StartsWith("obj/", StringComparison.Ordinal) || path.Contains("/obj/", StringComparison.Ordinal)) return true;
        if (path.EndsWith(".uid", StringComparison.Ordinal)) return true;
        return false;
    }
}
