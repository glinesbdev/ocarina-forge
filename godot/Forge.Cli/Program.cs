using System.Diagnostics;
using OcarinaForge.Import;
using OcarinaForge.Project;

namespace OcarinaForge.Cli;

public static class Program
{
    public static int Main(string[] args) => Generator.Run(args);
}

public static class Generator
{
    public static int Run(string[] args)
    {
        if (args.Length == 0 || args[0] is "-h" or "--help" or "help")
        {
            PrintHelp();
            return args.Length == 0 ? 2 : 0;
        }
        try
        {
            return args[0] switch
            {
                "new" => New(args.Skip(1).ToArray()),
                "models" => Models(args.Skip(1).ToArray()),
                "reimport" => Reimport(args.Skip(1).ToArray()),
                "check" => Check(args.Skip(1).ToArray()),
                _ => Unknown(args[0]),
            };
        }
        catch (Exception exc) when (exc is InvalidOperationException or ImportException)
        {
            Console.Error.WriteLine(exc.Message);
            return 1;
        }
    }

    static int New(string[] args)
    {
        var options = Options.Parse(args);
        if (options.Positionals.Count < 2)
        {
            Console.Error.WriteLine("usage: ocarina-forge-godot new <dump> <projectDir> [--godot <path>] [--banc <romfs path>]");
            return 2;
        }
        string dump = Path.GetFullPath(options.Positionals[0]);
        string project = Path.GetFullPath(options.Positionals[1]);
        if (File.Exists(ProjectPaths.Disk(project, ProjectPaths.Request)) && ManifestStore.LoadRequest(project).ModelsOnly)
            throw new InvalidOperationException("Choose an output folder for the map project, or use reimport for this model catalog.");
        var opened = DumpFolder.Open(dump);
        string banc = options.Get("banc") ?? FieldLoader.DefaultBanc;
        if (!opened.Exists(banc))
        {
            string bare = banc.EndsWith(".zs", StringComparison.Ordinal) ? banc[..^3] : banc + ".zs";
            if (opened.Exists(bare)) banc = bare;
        }
        if (!opened.Exists(banc))
            throw new ImportException($"placement file is not in this dump: {banc}");
        ProjectScaffold.Write(ProjectScaffold.FindTemplate(options.Get("template")), project, dump, banc);
        Console.WriteLine($"scaffolded {project}");
        return Generate(project, options.Get("godot"));
    }

    static int Models(string[] args)
    {
        var options = Options.Parse(args);
        if (options.Positionals.Count != 2 || options.Get("banc") is not null)
        {
            Console.Error.WriteLine("usage: ocarina-forge-godot models <dump> <projectDir> [--godot <path>] [--prefix <model or actor prefix>]");
            return 2;
        }
        var dump = DumpFolder.Open(options.Positionals[0]);
        string project = Path.GetFullPath(options.Positionals[1]);
        if (File.Exists(Path.Combine(project, "project.godot")))
            throw new InvalidOperationException("Choose a new catalog folder. Use reimport to update an existing catalog.");
        ProjectScaffold.Write(ProjectScaffold.FindTemplate(options.Get("template")), project, dump.Root, "",
            modelsOnly: true, modelPrefix: options.Get("prefix") ?? "");
        Console.WriteLine($"scaffolded model catalog {project}");
        return Generate(project, options.Get("godot"));
    }

    static int Reimport(string[] args)
    {
        var options = Options.Parse(args);
        if (options.Positionals.Count < 1)
        {
            Console.Error.WriteLine("usage: ocarina-forge-godot reimport <projectDir> [--godot <path>]");
            return 2;
        }
        string project = Path.GetFullPath(options.Positionals[0]);
        var request = ManifestStore.LoadRequest(project);
        if (!Directory.Exists(request.Dump))
            throw new ImportException($"dump folder does not exist: {request.Dump}");
        ProjectScaffold.Write(ProjectScaffold.FindTemplate(options.Get("template")), project, request.Dump, request.Banc,
            request.ModelsOnly, request.ModelPrefix);
        Console.WriteLine($"refreshed addon in {project}");
        return Generate(project, options.Get("godot"));
    }

    static int Check(string[] args)
    {
        var options = Options.Parse(args);
        if (options.Positionals.Count < 1)
        {
            Console.Error.WriteLine("usage: ocarina-forge-godot check <projectDir> [--godot <path>]");
            return 2;
        }
        string project = Path.GetFullPath(options.Positionals[0]);
        string? godot = FindGodot(options.Get("godot"));
        if (godot is null)
        {
            Console.Error.WriteLine("Godot 4.7 .NET was not found. Pass --godot or set GODOT.");
            return 2;
        }
        return RunProcess(godot, project, new[] { "--headless", "--path", project, "--", "--check" });
    }

    static int Generate(string project, string? godotArg)
    {
        int built = RunProcess("dotnet", project, new[] { "build", "-c", "Debug", "--nologo" });
        if (built != 0) return built;
        string? godot = FindGodot(godotArg);
        if (godot is null)
        {
            Console.Error.WriteLine("Godot 4.7 .NET was not found. Pass --godot or set GODOT, then run:");
            Console.Error.WriteLine($"  godot --headless --path \"{project}\" -- --generate");
            return 2;
        }
        Console.WriteLine($"godot {godot}");
        return RunProcess(godot, project, new[] { "--headless", "--path", project, "--", "--generate" });
    }

    static int RunProcess(string file, string workingDirectory, string[] arguments)
    {
        var start = new ProcessStartInfo(file)
        {
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
        };
        foreach (string argument in arguments) start.ArgumentList.Add(argument);
        using var process = Process.Start(start) ?? throw new InvalidOperationException($"could not start {file}");
        process.WaitForExit();
        return process.ExitCode;
    }

    static string? FindGodot(string? given)
    {
        foreach (string? candidate in new[] { given, Environment.GetEnvironmentVariable("GODOT") })
        {
            if (!string.IsNullOrWhiteSpace(candidate) && File.Exists(candidate)) return Path.GetFullPath(candidate);
        }
        string? path = Environment.GetEnvironmentVariable("PATH");
        if (path is not null)
        {
            foreach (string dir in path.Split(Path.PathSeparator))
            {
                foreach (string name in new[] { "godot", "godot4", "Godot" })
                {
                    string full = Path.Combine(dir, name);
                    if (File.Exists(full)) return full;
                }
            }
        }
        const string linux = "/tmp/godot/Godot_v4.7.2-stable_mono_linux_x86_64/Godot_v4.7.2-stable_mono_linux.x86_64";
        return File.Exists(linux) ? linux : null;
    }

    static int Unknown(string command)
    {
        Console.Error.WriteLine($"unknown command {command}");
        PrintHelp();
        return 2;
    }

    static void PrintHelp()
    {
        Console.WriteLine("""
            ocarina-forge-godot new <dump> <projectDir> [--godot <Godot exe>] [--banc <romfs path>] [--template <dir>]
            ocarina-forge-godot models <dump> <projectDir> [--godot <Godot exe>] [--prefix <model or actor prefix>] [--template <dir>]
            ocarina-forge-godot reimport <projectDir> [--godot <Godot exe>] [--template <dir>]
            ocarina-forge-godot check <projectDir> [--godot <Godot exe>]

            Writes a Godot 4.7 project. Models, materials, textures, and maps/HyruleField.tscn
            are native resources. .ocarina/manifest.json records each output, its romfs sources, and a hash.
            A second run leaves outputs whose sources have not changed.
            """);
    }

    sealed class Options
    {
        public List<string> Positionals { get; } = new();
        readonly Dictionary<string, string> _flags = new(StringComparer.Ordinal);

        public string? Get(string name) => _flags.TryGetValue(name, out var value) ? value : null;

        public static Options Parse(string[] args)
        {
            var options = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                if (arg is "--godot" or "--banc" or "--template" or "--prefix")
                {
                    if (i + 1 >= args.Length) throw new InvalidOperationException($"{arg} needs a value");
                    options._flags[arg[2..]] = args[++i];
                }
                else if (arg.StartsWith("--", StringComparison.Ordinal))
                    throw new InvalidOperationException($"unknown option {arg}");
                else options.Positionals.Add(arg);
            }
            return options;
        }
    }
}
