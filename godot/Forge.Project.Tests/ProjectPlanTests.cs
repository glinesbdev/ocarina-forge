using OcarinaForge.Cli;
using OcarinaForge.Import;
using Xunit;

namespace OcarinaForge.Project.Tests;

public class ProjectPlanTests
{
    [Fact]
    public void SyntheticFieldPlansAStableLayout()
    {
        string root = SyntheticDump.Write(Temp("plan"));
        var dump = DumpFolder.Open(root);
        var field = FieldLoader.Load(dump);
        var job = ProjectPlan.Build(dump, field, null, Temp("empty-project"));
        var model = Assert.Single(job.Models);
        Assert.Equal("Obj_Tree", model.Gyaml);
        Assert.Contains(model.Outputs, output => output.ResPath == "res://models/Obj_Tree/Obj_Tree.tscn" && output.Kind == "model");
        Assert.Contains(model.Sources, source => source.Replace('\\', '/').EndsWith("Model/Obj_Tree.Obj_Tree.bfres", StringComparison.Ordinal));
        Assert.False(model.Current);
        Assert.Equal(ProjectPaths.MapScene, job.Map.Outputs[0].ResPath);
        Assert.False(job.Map.Current);
        Assert.Contains(job.Map.Sources, source => source.Contains("HyruleField_Child_Static", StringComparison.Ordinal));
        Assert.Contains(job.Map.Sources, source => source.Contains("Field.tscb", StringComparison.Ordinal));
        Assert.Equal(ProjectPlan.ImporterVersion, "godot-project-1");
        if (model.Model.Skeleton.Count > 0)
            Assert.All(model.Model.Skeleton, bone => Assert.False(string.IsNullOrWhiteSpace(bone.Name)));
    }

    [Fact]
    public void UnchangedSourcesAreCurrentAndAByteChangeIsNot()
    {
        string root = SyntheticDump.Write(Temp("hash"));
        string project = Temp("hash-project");
        var dump = DumpFolder.Open(root);
        var field = FieldLoader.Load(dump);
        var first = ProjectPlan.Build(dump, field, null, project);
        var manifest = ProjectPlan.ToManifest(first);
        foreach (var output in first.Models.SelectMany(model => model.Outputs).Concat(first.Map.Outputs).Concat(first.Textures.Select(texture => new PlannedOutput { ResPath = texture.ResPath })))
        {
            string disk = ProjectPaths.Disk(project, output.ResPath);
            Directory.CreateDirectory(Path.GetDirectoryName(disk)!);
            File.WriteAllBytes(disk, new byte[] { 1 });
        }
        ManifestStore.Save(project, manifest);
        var second = ProjectPlan.Build(dump, field, ManifestStore.Load(project), project);
        Assert.All(second.Models, model => Assert.True(model.Current));
        Assert.All(second.Textures, texture => Assert.True(texture.Current));
        Assert.True(second.Map.Current);

        string modelFile = Path.Combine(dump.Romfs, "Model", "Obj_Tree.Obj_Tree.bfres");
        File.AppendAllText(modelFile, "x");
        var third = ProjectPlan.Build(dump, field, ManifestStore.Load(project), project);
        Assert.False(third.Models[0].Current);
        Assert.NotEqual(first.Models[0].Hash, third.Models[0].Hash);
        Assert.False(third.Map.Current);
    }

    [Fact]
    public void SafeNamesDropPathCharacters()
    {
        Assert.Equal("Land_kokiri_Plant_A", ProjectPaths.Safe("Land/kokiri Plant:A"));
        Assert.Equal("unnamed", ProjectPaths.Safe("***"));
    }

    [Fact]
    public void HeadlessGenerateWritesScenesAndSkipsTheSecondRun()
    {
        string? godot = Environment.GetEnvironmentVariable("GODOT");
        if (string.IsNullOrEmpty(godot) || !File.Exists(godot))
        {
            if (Environment.GetEnvironmentVariable("REQUIRE_GODOT") == "1")
                Assert.Fail("GODOT must point at the Godot 4.7 .NET binary");
            return;
        }
        string dump = SyntheticDump.Write(Temp("gen-dump"));
        string project = Temp("gen-project");
        Assert.Equal(0, Generator.Run(new[] { "new", dump, project, "--godot", godot }));
        string scene = Path.Combine(project, "models", "Obj_Tree", "Obj_Tree.tscn");
        string map = Path.Combine(project, "maps", "HyruleField.tscn");
        string manifest = Path.Combine(project, ".ocarina", "manifest.json");
        Assert.True(File.Exists(scene));
        Assert.True(File.Exists(map));
        Assert.True(File.Exists(manifest));
        string modelText = File.ReadAllText(scene);
        Assert.Contains("Skeleton3D", modelText);
        Assert.Contains("AnimationPlayer", modelText);
        Assert.Contains("res://models/Obj_Tree/Obj_Tree.tscn", File.ReadAllText(manifest));
        Assert.True(Directory.EnumerateFiles(project, "*.res", SearchOption.AllDirectories).Any());
        Assert.Equal(0, Generator.Run(new[] { "check", project, "--godot", godot }));
        string mesh = Path.Combine(project, "models", "Obj_Tree", "meshes", "Trunk_lod0.res");
        DateTime written = File.GetLastWriteTimeUtc(mesh);
        Assert.Equal(0, Generator.Run(new[] { "reimport", project, "--godot", godot }));
        Assert.Equal(written, File.GetLastWriteTimeUtc(mesh));
        Assert.Contains("Skeleton3D", File.ReadAllText(scene));
        Assert.Contains("instance=", File.ReadAllText(map));
    }

    static string Temp(string name) => Path.Combine(Path.GetTempPath(), "of-project-" + name + "-" + Guid.NewGuid().ToString("N"));
}
