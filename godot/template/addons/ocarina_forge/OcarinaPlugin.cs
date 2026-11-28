using Godot;
using OcarinaForge.Project;

namespace OcarinaForge.Generated;

/// <summary>Editor dock. Reimport reads <c>.ocarina/project.json</c> and runs the same writer as the CLI.</summary>
[Tool]
public partial class OcarinaPlugin : EditorPlugin
{
    Control? _dock;

    public override void _EnterTree()
    {
        var root = new VBoxContainer { Name = "OcarinaForge" };
        var path = new LineEdit { PlaceholderText = "Dump folder" };
        string projectDir = ProjectSettings.GlobalizePath("res://");
        ProjectRequest request;
        try
        {
            request = ManifestStore.LoadRequest(projectDir);
            path.Text = request.Dump;
        }
        catch (InvalidOperationException)
        {
            request = new ProjectRequest();
        }
        var button = new Button { Text = "Reimport from dump" };
        var status = new Label { AutowrapMode = TextServer.AutowrapMode.WordSmart };
        button.Pressed += () =>
        {
            request.Dump = path.Text.Trim();
            ManifestStore.SaveRequest(projectDir, request);
            status.Text = "Importing. Watch the Output panel. The editor waits until it finishes.";
            int code = ProjectEmitter.Generate();
            status.Text = code == 0
                ? "Import finished. New files are in FileSystem."
                : "Import failed. The Output panel has the reason.";
            EditorInterface.Singleton.GetResourceFilesystem().Scan();
        };
        root.AddChild(new Label { Text = "Ocarina Forge" });
        root.AddChild(path);
        root.AddChild(button);
        root.AddChild(status);
        _dock = root;
#pragma warning disable CS0618 // Godot 4.7 prefers EditorDock. This dock still opens on the right.
        AddControlToDock(DockSlot.RightUl, root);
#pragma warning restore CS0618
    }

    public override void _ExitTree()
    {
#pragma warning disable CS0618
        if (_dock is not null) RemoveControlFromDocks(_dock);
#pragma warning restore CS0618
        _dock?.QueueFree();
        _dock = null;
    }
}
