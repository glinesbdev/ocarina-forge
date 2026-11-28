using Godot;
using OcarinaForge.Project;

namespace OcarinaForge.Generated;

/// <summary>
/// Builds the terrain <see cref="Texture2DArray"/> from the layer PNGs.
/// <c>ResourceSaver</c> writes a <c>Texture2DArray</c> whose images are null on the next load
/// (<c>img.is_null()</c>, <c>_create_from_images != OK</c>), which is the editor's magenta checker.
/// The PNGs are the copy that survives. This runs in the editor and at play.
/// </summary>
[Tool]
public partial class TerrainAlbedo : MeshInstance3D
{
    public override void _EnterTree() => Bind();

    public void Bind()
    {
        if (MaterialOverride is not ShaderMaterial material) return;
        var images = new Godot.Collections.Array<Image>();
        for (int i = 0; ; i++)
        {
            string disk = ProjectSettings.GlobalizePath(ProjectPaths.TerrainLayer(i));
            if (!File.Exists(disk)) break;
            var image = Image.LoadFromFile(disk);
            if (image is null || image.IsEmpty()) break;
            if (image.GetFormat() != Image.Format.Rgba8) image.Convert(Image.Format.Rgba8);
            if (!image.HasMipmaps()) image.GenerateMipmaps();
            images.Add(image);
        }
        if (images.Count == 0) return;
        var array = new Texture2DArray();
        if (array.CreateFromImages(images) != Error.Ok) return;
        material.SetShaderParameter("albedo", array);
    }
}
