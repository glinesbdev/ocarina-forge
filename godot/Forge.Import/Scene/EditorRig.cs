namespace OcarinaForge.Import;

/// <summary>
/// The browser editor's three.js light rig (frontend/src/viewport.js).
/// This is not a measured in-game sun. Longshot's lighting capture leaves sun, ambient, and exposure null.
/// </summary>
public static class EditorRig
{
    public const float BackgroundR = 0.078f;
    public const float BackgroundG = 0.106f;
    public const float BackgroundB = 0.086f;
    public const float AmbientR = 0.557f;
    public const float AmbientG = 0.627f;
    public const float AmbientB = 0.565f;
    public const float AmbientEnergy = 0.55f;
    public const float Exposure = 1.05f;
    public const float SunR = 1f;
    public const float SunG = 0.949f;
    public const float SunB = 0.839f;
    public const float SunEnergy = 2.6f;
    public const float SunX = 18f;
    public const float SunY = 36f;
    public const float SunZ = 14f;

    public const float LodNearEnd = 72f;
    public const float LodFarEnd = 8000f;
}

/// <summary>Flat albedo used when a material has no uploaded texture. Same colours as the viewer.</summary>
public static class FlatColour
{
    public static (float R, float G, float B) For(string material, string texture)
    {
        string blob = (material + " " + texture).ToLowerInvariant();
        if (blob.Contains("leaf") || blob.Contains("plant") || blob.Contains("grass") || blob.Contains("vine") || blob.Contains("bush"))
            return (0.25f, 0.38f, 0.18f);
        if (blob.Contains("rock") || blob.Contains("stone") || blob.Contains("cliff"))
            return (0.45f, 0.42f, 0.38f);
        if (blob.Contains("wood") || blob.Contains("trunk") || blob.Contains("bark"))
            return (0.42f, 0.32f, 0.2f);
        return (0.55f, 0.52f, 0.48f);
    }
}
