using System.Text.Json;
using Godot;
using OcarinaForge.Import;
using OcarinaForge.Project;

namespace OcarinaForge.Generated;

/// <summary>
/// Shell for a generated project. <c>--generate</c> writes the scenes, <c>--check</c> loads them,
/// and <c>--benchmark</c> flies field-loop-160m. Play in the editor to fly the map.
/// </summary>
public partial class Main : Node3D
{
    Camera3D _camera = null!;
    Label _stats = null!;
    Vector2 _look;
    bool _fly = true;
    bool _open;
    bool _benchmark;
    string _benchmarkOut = "";
    int _instances;
    int _meshes;
    long _geometryBytes;
    long _bc7Bytes;
    ManifestSummary? _summary;
    readonly FieldFlight _flight = new();
    readonly List<Dictionary<string, double?>> _samples = new();
    int _warmup;
    string _status = "No map yet.";

    public override void _Ready()
    {
        _camera = GetNode<Camera3D>("Camera");
        _stats = GetNode<Label>("Overlay/Stats");
        RenderingServer.ViewportSetMeasureRenderTime(GetViewport().GetViewportRid(), true);
        var args = Parse(OS.GetCmdlineUserArgs());
        if (args.ContainsKey("generate"))
        {
            GetTree().Quit(ProjectEmitter.Generate());
            return;
        }
        if (args.ContainsKey("check"))
        {
            GetTree().Quit(ProjectEmitter.Check());
            return;
        }
        if (args.ContainsKey("probe-save"))
        {
            GetTree().Quit(ProjectEmitter.Probe(false));
            return;
        }
        if (args.ContainsKey("probe-load"))
        {
            GetTree().Quit(ProjectEmitter.Probe(true));
            return;
        }
        _benchmark = args.ContainsKey("benchmark");
        _benchmarkOut = args.GetValueOrDefault("benchmark-out") ?? "";
        if (ResourceLoader.Exists(ProjectPaths.MapScene)) OpenMap();
        else _status = "No map yet. Run ocarina-forge-godot new, or Reimport in the Ocarina Forge dock.";
        if (DisplayServer.GetName() == "headless" && !_benchmark)
        {
            GD.Print(_open ? "project ready" : "project has no map");
            GetTree().Quit(_open ? 0 : 2);
        }
    }

    public override void _UnhandledInput(InputEvent @event)
    {
        if (!_fly || _benchmark || !_open) return;
        if (@event is InputEventMouseMotion motion && Input.MouseMode == Input.MouseModeEnum.Captured)
        {
            _look.X -= motion.Relative.X * 0.003f;
            _look.Y = Mathf.Clamp(_look.Y - motion.Relative.Y * 0.003f, -1.4f, 1.4f);
            var basis = Basis.Identity;
            basis = basis.Rotated(Vector3.Up, _look.X).Rotated(basis.Rotated(Vector3.Up, _look.X) * Vector3.Right, _look.Y);
            _camera.Basis = basis;
        }
        else if (@event is InputEventMouseButton button && button.Pressed && button.ButtonIndex == MouseButton.Right)
            Input.MouseMode = Input.MouseMode == Input.MouseModeEnum.Captured ? Input.MouseModeEnum.Visible : Input.MouseModeEnum.Captured;
        else if (@event is InputEventKey key && key.Pressed && key.Keycode == Key.Escape)
            Input.MouseMode = Input.MouseModeEnum.Visible;
    }

    public override void _Process(double delta)
    {
        if (!_open) return;
        if (_fly && !_benchmark)
        {
            var wish = Vector3.Zero;
            if (Input.IsKeyPressed(Key.W)) wish -= _camera.Basis.Z;
            if (Input.IsKeyPressed(Key.S)) wish += _camera.Basis.Z;
            if (Input.IsKeyPressed(Key.A)) wish -= _camera.Basis.X;
            if (Input.IsKeyPressed(Key.D)) wish += _camera.Basis.X;
            if (Input.IsKeyPressed(Key.E) || Input.IsKeyPressed(Key.Space)) wish += Vector3.Up;
            if (Input.IsKeyPressed(Key.Q)) wish -= Vector3.Up;
            float speed = Input.IsKeyPressed(Key.Shift) ? 180f : 40f;
            if (wish.LengthSquared() > 0) _camera.Position += wish.Normalized() * speed * (float)delta;
        }
        if (_benchmark)
        {
            if (_warmup < 30) _warmup++;
            else if (!_flight.Done)
            {
                var pose = _flight.Sample(FieldFlight.FrameDt);
                _camera.Position = new Vector3((float)pose.X, (float)pose.Y, (float)pose.Z);
                _camera.LookAt(new Vector3((float)pose.LookX, (float)pose.LookY, (float)pose.LookZ), Vector3.Up);
                _samples.Add(Sample());
            }
            else
            {
                WriteBenchmark();
                GetTree().Quit();
                return;
            }
        }
        Paint();
    }

    void OpenMap()
    {
        var map = ResourceLoader.Load<PackedScene>(ProjectPaths.MapScene).Instantiate<Node3D>();
        map.Name = "Field";
        AddChild(map);
        _open = true;
        _status = "Opened maps/HyruleField.tscn.";
        _meshes = CountMeshes(map);
        _instances = map.GetNodeOrNull("Actors")?.GetChildCount() ?? 0;
        var manifest = ManifestStore.Load(ProjectSettings.GlobalizePath("res://"));
        _summary = manifest?.Summary;
        _geometryBytes = _summary?.GeometryBytes ?? 0;
        _bc7Bytes = _summary?.Bc7Bytes ?? 0;
        _camera.Position = new Vector3(0, 190, 0);
        _camera.LookAt(new Vector3(0, 150, -48), Vector3.Up);
        GD.Print($"field actors {_instances} meshes {_meshes}");
    }

    Dictionary<string, double?> Sample()
    {
        var rid = GetViewport().GetViewportRid();
        double cpu = RenderingServer.ViewportGetMeasuredRenderTimeCpu(rid) + RenderingServer.GetFrameSetupTimeCpu();
        double gpu = RenderingServer.ViewportGetMeasuredRenderTimeGpu(rid);
        double draws = RenderingServer.GetRenderingInfo(RenderingServer.RenderingInfo.TotalDrawCallsInFrame);
        double objects = RenderingServer.GetRenderingInfo(RenderingServer.RenderingInfo.TotalObjectsInFrame);
        return new Dictionary<string, double?>
        {
            ["cpuMs"] = cpu,
            ["gpuMs"] = gpu > 0 ? gpu : null,
            ["draws"] = draws,
            ["batches"] = draws,
            ["objects"] = objects,
            ["geomBytes"] = _geometryBytes,
            ["bc7Bytes"] = _bc7Bytes,
        };
    }

    void WriteBenchmark()
    {
        var summary = BenchmarkSummary.Summarize(_samples);
        var result = new Dictionary<string, object?>
        {
            ["version"] = 1,
            ["scene"] = FieldFlight.Scene,
            ["path"] = FieldFlight.PathName,
            ["renderer"] = "Godot 4.7.2 .NET",
            ["settled"] = true,
            ["frames"] = _samples.Count,
            ["summary"] = summary,
            ["geomBytes"] = _geometryBytes,
            ["bc7Bytes"] = _bc7Bytes,
            ["instances"] = _instances,
            ["load"] = _summary is null ? null : new Dictionary<string, object?>
            {
                ["actorsLoaded"] = _summary.ActorsLoaded,
                ["actorsMissing"] = _summary.ActorsMissing,
                ["actorsSkipped"] = _summary.ActorsSkipped,
                ["texturesOk"] = _summary.TexturesOk,
                ["texturesFailed"] = _summary.TexturesFailed,
                ["texturesMissing"] = _summary.TexturesMissing,
                ["materialsUnbound"] = _summary.MaterialsUnbound,
                ["shapesFailed"] = _summary.ShapesFailed,
                ["shapesOmitted"] = _summary.ShapesOmitted,
            },
            ["note"] = "Same camera path and 1/60 s step as the browser field-loop-160m. The scene is the generated maps/HyruleField.tscn. cpuMs is Godot's measured render CPU plus frame setup. gpuMs is present when the renderer reports it.",
        };
        string json = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        string path = _benchmarkOut.Length > 0 ? _benchmarkOut : Path.Combine(Directory.GetCurrentDirectory(), "field-loop-160m.json");
        File.WriteAllText(path, json);
        GD.Print(json);
        GD.Print("wrote " + path);
    }

    void Paint()
    {
        var rid = GetViewport().GetViewportRid();
        double cpu = RenderingServer.ViewportGetMeasuredRenderTimeCpu(rid) + RenderingServer.GetFrameSetupTimeCpu();
        double gpu = RenderingServer.ViewportGetMeasuredRenderTimeGpu(rid);
        double draws = RenderingServer.GetRenderingInfo(RenderingServer.RenderingInfo.TotalDrawCallsInFrame);
        double objects = RenderingServer.GetRenderingInfo(RenderingServer.RenderingInfo.TotalObjectsInFrame);
        double vram = RenderingServer.GetRenderingInfo(RenderingServer.RenderingInfo.VideoMemUsed);
        double frame = 1000.0 / Math.Max(1, Engine.GetFramesPerSecond());
        _stats.Text =
            $"Ocarina Forge  Godot 4.7.2\n{_status}\n" +
            $"frame {frame:0.00} ms   cpu {cpu:0.00} ms   gpu {(gpu > 0 ? $"{gpu:0.00} ms" : "n/a")}\n" +
            $"draws {draws}   objects {objects}   instances {_instances}   meshes {_meshes}\n" +
            $"vram {vram / (1024.0 * 1024):0} MB\n" +
            $"geometry {_geometryBytes / (1024.0 * 1024):0.0} MB   bc7 {_bc7Bytes / (1024.0 * 1024):0.0} MB\n" +
            "Right mouse look, WASD fly, Q/E vertical, Shift faster, Esc release mouse";
    }

    static int CountMeshes(Node node)
    {
        int count = node is MeshInstance3D ? 1 : 0;
        foreach (var child in node.GetChildren()) count += CountMeshes(child);
        return count;
    }

    static Dictionary<string, string> Parse(string[] args)
    {
        var found = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            if (!arg.StartsWith("--", StringComparison.Ordinal)) continue;
            string name = arg[2..];
            if (i + 1 < args.Length && !args[i + 1].StartsWith("--", StringComparison.Ordinal))
                found[name] = args[++i];
            else found[name] = "";
        }
        return found;
    }
}
