using System.Text.Json;
using Godot;
using OcarinaForge.Import;

namespace OcarinaForge.Viewer;

public partial class Main : Node3D
{
    const string SettingsPath = "user://ocarina-forge.cfg";
    Camera3D _camera = null!;
    Label _stats = null!;
    Vector2 _look;
    bool _fly = true;
    bool _benchmark;
    bool _selfTest;
    string _benchmarkOut = "";
    string? _cacheDir;
    ImportedField? _field;
    int _instances;
    int _drawables;
    readonly FieldFlight _flight = new();
    readonly List<Dictionary<string, double?>> _samples = new();
    int _warmup;
    string _status = "Choose a dump folder.";
    bool _failed;

    public override void _Ready()
    {
        _camera = GetNode<Camera3D>("Camera");
        _stats = GetNode<Label>("Overlay/Stats");
        RenderingServer.ViewportSetMeasureRenderTime(GetViewport().GetViewportRid(), true);
        var args = Parse(OS.GetCmdlineUserArgs());
        _benchmark = args.ContainsKey("benchmark");
        _selfTest = args.ContainsKey("self-test");
        _benchmarkOut = args.GetValueOrDefault("benchmark-out") ?? "";
        _cacheDir = args.GetValueOrDefault("cache");
        if (_selfTest)
        {
            RunSelfTest();
            return;
        }
        string dump = args.GetValueOrDefault("dump") ?? LoadSetting();
        if (dump.Length > 0)
        {
            OpenDump(dump);
            return;
        }
        if (DisplayServer.GetName() == "headless")
        {
            GD.PrintErr("Pass -- --dump <folder> or -- --self-test. Headless mode has no folder picker.");
            GetTree().Quit(2);
            return;
        }
        var dialog = new FileDialog
        {
            FileMode = FileDialog.FileModeEnum.OpenDir,
            Access = FileDialog.AccessEnum.Filesystem,
            Title = "Dump folder (the one that contains romfs/)",
            Size = new Vector2I(900, 560),
        };
        dialog.DirSelected += path =>
        {
            SaveSetting(path);
            OpenDump(path);
        };
        dialog.Canceled += () =>
        {
            _status = "No dump selected.";
            GetTree().Quit(2);
        };
        AddChild(dialog);
        dialog.PopupCentered();
    }

    public override void _UnhandledInput(InputEvent @event)
    {
        if (!_fly || _benchmark) return;
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
        if (_fly && !_benchmark && _field is not null)
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
        if (_benchmark && _field is not null)
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
                _benchmark = false;
                GetTree().Quit();
                return;
            }
        }
        Paint();
    }

    void OpenDump(string path)
    {
        try
        {
            var dump = DumpFolder.Open(path);
            string cacheDir = _cacheDir ?? ImportCache.DefaultDirectory();
            string key = ImportCache.KeyFor(dump, FieldLoader.DefaultBanc);
            string cachePath = ImportCache.PathFor(cacheDir, key);
            var cached = ImportCache.TryLoad(cachePath);
            if (cached is not null)
            {
                _field = cached;
                _status = "Loaded the cache.";
            }
            else
            {
                _field = FieldLoader.Load(dump);
                _status = "Imported from the dump.";
            }
            if (TexturePrep.Apply(_field) || cached is null)
            {
                ImportCache.Save(cachePath, _field);
                if (cached is not null) _status = "Updated the cache.";
            }
            var node = FieldScene.Build(_field, out _instances, out _drawables);
            AddChild(node);
            _camera.Position = new Vector3(0, 190, 0);
            _camera.LookAt(new Vector3(0, 150, -48), Vector3.Up);
            GD.Print($"field actors {_field.Actors.Count} instances {_instances} terrain {_field.Terrain?.Positions.Count ?? 0} bc7 {_field.Bc7Bytes}");
            GD.Print(_field.Report.Format());
        }
        catch (Exception exc)
        {
            _failed = true;
            _status = exc.Message;
            GD.PrintErr(exc);
            if (DisplayServer.GetName() == "headless") GetTree().Quit(1);
        }
    }

    void RunSelfTest()
    {
        string root = SyntheticDump.Write(Path.Combine(Path.GetTempPath(), "of-godot-" + Guid.NewGuid().ToString("N")));
        try
        {
            OpenDump(root);
            if (_failed || _field is null || _field.Actors.Count(actor => actor.Model is not null) != 3 || _field.Terrain is null)
                throw new InvalidOperationException(_status);
            GD.Print(TexturePrep.Probe());
            GD.Print("self-test ok");
            GetTree().Quit();
        }
        catch (Exception exc)
        {
            GD.PrintErr(exc);
            GetTree().Quit(1);
        }
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
            ["geomBytes"] = _field?.GeometryBytes,
            ["bc7Bytes"] = _field?.Bc7Bytes,
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
            ["geomBytes"] = _field?.GeometryBytes,
            ["bc7Bytes"] = _field?.Bc7Bytes,
            ["instances"] = _instances,
            ["load"] = _field is null ? null : new Dictionary<string, object?>
            {
                ["actorsLoaded"] = _field.Report.ActorsLoaded,
                ["actorsMissing"] = _field.Report.ActorsMissing,
                ["actorsSkipped"] = _field.Report.ActorsSkipped,
                ["texturesOk"] = _field.Report.TexturesOk,
                ["texturesFailed"] = _field.Report.TexturesFailed,
                ["shapesFailed"] = _field.Report.ShapesFailed,
                ["missing"] = _field.Report.Missing.OrderByDescending(item => item.Value).Take(8)
                    .Select(item => new Dictionary<string, object> { ["name"] = item.Key, ["count"] = item.Value }).ToList(),
                ["reasons"] = _field.Report.Reasons.OrderByDescending(item => item.Value).Take(8)
                    .Select(item => new Dictionary<string, object> { ["reason"] = item.Key, ["count"] = item.Value }).ToList(),
            },
            ["note"] = "Same camera path and 1/60 s step as the browser field-loop-160m. cpuMs is Godot's measured render CPU plus frame setup. gpuMs is present when the renderer reports it. submitMs, updateMs, writeTex, heapBytes and the other browser counters are absent. batches is the draw-call count; Godot does not report three.js batch objects.",
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
        double textures = RenderingServer.GetRenderingInfo(RenderingServer.RenderingInfo.TextureMemUsed);
        double frame = 1000.0 / Math.Max(1, Engine.GetFramesPerSecond());
        _stats.Text =
            $"Ocarina Forge  Godot 4.7.2\n{_status}\n" +
            $"frame {frame:0.00} ms   cpu {cpu:0.00} ms   gpu {(gpu > 0 ? $"{gpu:0.00} ms" : "n/a")}\n" +
            $"draws {draws}   objects {objects}   instances {_instances}   meshes {_drawables}\n" +
            $"vram {vram / (1024.0 * 1024):0} MB   textures {textures / (1024.0 * 1024):0} MB\n" +
            $"geometry {(_field?.GeometryBytes ?? 0) / (1024.0 * 1024):0.0} MB   bc7 {(_field?.Bc7Bytes ?? 0) / (1024.0 * 1024):0.0} MB\n" +
            "Right mouse look, WASD fly, Q/E vertical, Shift faster, Esc release mouse";
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
            else found[name] = "true";
        }
        return found;
    }

    static string LoadSetting()
    {
        var cfg = new ConfigFile();
        if (cfg.Load(SettingsPath) != Error.Ok) return "";
        return cfg.GetValue("dump", "path", "").AsString();
    }

    static void SaveSetting(string path)
    {
        var cfg = new ConfigFile();
        cfg.Load(SettingsPath);
        cfg.SetValue("dump", "path", path);
        cfg.Save(SettingsPath);
    }
}
