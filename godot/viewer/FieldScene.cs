using Godot;
using OcarinaForge.Import;

namespace OcarinaForge.Viewer;

/// <summary>Builds ArrayMesh nodes from an <see cref="ImportedField"/>. Godot objects stay on the main thread.</summary>
public static class FieldScene
{
    public static Node3D Build(ImportedField field, out int instances, out int drawables)
    {
        var root = new Node3D { Name = "Field" };
        var textures = new Dictionary<string, Texture2D>(StringComparer.OrdinalIgnoreCase);
        var materials = new Dictionary<string, Material>(StringComparer.Ordinal);
        int instanceCount = 0;
        int drawableCount = 0;
        var groups = field.Actors.Where(actor => actor.Model is not null).GroupBy(actor => actor.ModelPath);
        foreach (var group in groups)
        {
            var actors = group.ToList();
            var model = actors[0].Model!;
            var shared = new Dictionary<string, CompressedTexture>(StringComparer.OrdinalIgnoreCase);
            foreach (var actor in actors)
                foreach (var (name, image) in actor.Textures)
                    shared[name] = image;
            bool hasFar = model.Parts.Any(part => part.Lod > 0);
            foreach (var part in model.Parts)
            {
                if (part.OmitReason.Length > 0 || part.Helper.Length > 0) continue;
                if (part.Lod > 1) continue;
                if (part.Positions.Count == 0 || part.Indices.Count < 3) continue;
                var mesh = MakeMesh(part);
                var material = MakeMaterial(part, shared, textures, materials, field.Report);
                var transforms = actors.Select(actor => ToTransform(actor.Placement.Matrix())).ToArray();
                var body = Spawn(part.Name + (part.Lod == 0 ? "" : $"_lod{part.Lod}"), mesh, material, transforms);
                ApplyRange(body, part, hasFar);
                root.AddChild(body);
                instanceCount += transforms.Length;
                drawableCount++;
            }
        }
        if (field.Terrain is { Positions.Count: > 0, Indices.Count: >= 3 })
        {
            var mesh = MakeMesh(field.Terrain);
            var material = TerrainMaterial(field);
            var body = Spawn("terrain", mesh, material, new[] { Transform3D.Identity });
            root.AddChild(body);
            root.AddChild(Occluder(field.Terrain));
            instanceCount++;
            drawableCount++;
        }
        instances = instanceCount;
        drawables = drawableCount;
        return root;
    }

    static GeometryInstance3D Spawn(string name, ArrayMesh mesh, Material material, Transform3D[] transforms)
    {
        mesh.SurfaceSetMaterial(0, material);
        if (transforms.Length >= 2)
        {
            var multi = new MultiMesh
            {
                TransformFormat = MultiMesh.TransformFormatEnum.Transform3D,
                Mesh = mesh,
                InstanceCount = transforms.Length,
            };
            for (int i = 0; i < transforms.Length; i++) multi.SetInstanceTransform(i, transforms[i]);
            return new MultiMeshInstance3D { Name = name, Multimesh = multi, CastShadow = GeometryInstance3D.ShadowCastingSetting.Off };
        }
        return new MeshInstance3D
        {
            Name = name,
            Mesh = mesh,
            Transform = transforms.Length == 1 ? transforms[0] : Transform3D.Identity,
            CastShadow = GeometryInstance3D.ShadowCastingSetting.Off,
        };
    }

    static void ApplyRange(GeometryInstance3D body, ImportedPart part, bool hasFar)
    {
            if (hasFar && part.Lod == 0)
        {
            body.VisibilityRangeEnd = EditorRig.LodNearEnd;
            body.VisibilityRangeEndMargin = 0;
            body.VisibilityRangeFadeMode = GeometryInstance3D.VisibilityRangeFadeModeEnum.Disabled;
        }
        else if (part.Lod > 0)
        {
            body.VisibilityRangeBegin = EditorRig.LodNearEnd;
            body.VisibilityRangeBeginMargin = 0;
            body.VisibilityRangeEnd = EditorRig.LodFarEnd;
            body.VisibilityRangeFadeMode = GeometryInstance3D.VisibilityRangeFadeModeEnum.Disabled;
        }
        else
        {
            float radius = Radius(part);
            body.VisibilityRangeEnd = radius < 1.2f ? 56 : radius < 3f ? 96 : radius < 8f ? 180 : 8000;
            body.VisibilityRangeFadeMode = GeometryInstance3D.VisibilityRangeFadeModeEnum.Self;
        }
    }

    static float Radius(ImportedPart part)
    {
        double max = 0;
        foreach (var point in part.Positions)
        {
            double d = point.X * point.X + point.Y * point.Y + point.Z * point.Z;
            if (d > max) max = d;
        }
        return (float)Math.Sqrt(max);
    }

    static ArrayMesh MakeMesh(ImportedPart part)
    {
        var vertices = new Vector3[part.Positions.Count];
        for (int i = 0; i < vertices.Length; i++)
        {
            var point = part.Positions[i];
            vertices[i] = new Vector3((float)point.X, (float)point.Y, (float)point.Z);
        }
        Vector2[]? uvs = null;
        if (part.Uvs is { Count: > 0 } && part.Uvs.Count == part.Positions.Count)
        {
            uvs = new Vector2[part.Uvs.Count];
            for (int i = 0; i < uvs.Length; i++) uvs[i] = new Vector2((float)part.Uvs[i].U, 1f - (float)part.Uvs[i].V);
        }
        Vector3[]? normals = null;
        if (part.Normals is { Count: > 0 } && part.Normals.Count == part.Positions.Count)
        {
            normals = new Vector3[part.Normals.Count];
            for (int i = 0; i < normals.Length; i++)
            {
                var normal = part.Normals[i];
                normals[i] = new Vector3((float)normal.X, (float)normal.Y, (float)normal.Z);
            }
        }
        var indices = part.Indices.Select(index => index).ToArray();
        Vector2[]? uv2 = null;
        Color[]? colors = null;
        if (part.MaterialLayers is { Count: > 0 } && part.MaterialLayers.Count == part.Positions.Count)
        {
            uv2 = new Vector2[part.MaterialLayers.Count];
            colors = new Color[part.MaterialLayers.Count];
            for (int i = 0; i < uv2.Length; i++)
            {
                float other = part.MaterialLayerB is { Count: > 0 } && i < part.MaterialLayerB.Count ? part.MaterialLayerB[i] : -1;
                float mix = part.MaterialBlend is { Count: > 0 } && i < part.MaterialBlend.Count ? part.MaterialBlend[i] : 0;
                uv2[i] = new Vector2(part.MaterialLayers[i], other);
                colors[i] = new Color(mix, 0, 0, 1);
            }
        }
        var arrays = new Godot.Collections.Array();
        arrays.Resize((int)Mesh.ArrayType.Max);
        arrays[(int)Mesh.ArrayType.Vertex] = vertices;
        arrays[(int)Mesh.ArrayType.Index] = indices;
        if (uvs is not null) arrays[(int)Mesh.ArrayType.TexUV] = uvs;
        if (uv2 is not null) arrays[(int)Mesh.ArrayType.TexUV2] = uv2;
        if (colors is not null) arrays[(int)Mesh.ArrayType.Color] = colors;
        if (normals is not null) arrays[(int)Mesh.ArrayType.Normal] = normals;
        var mesh = new ArrayMesh();
        mesh.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles, arrays);
        return mesh;
    }

    static Material MakeMaterial(ImportedPart part, Dictionary<string, CompressedTexture> textures, Dictionary<string, Texture2D> uploaded, Dictionary<string, Material> cache, LoadReport report)
    {
        string key = part.Water ? "water" : $"{part.Alpha}|{part.DoubleSided}|{part.Texture}";
        if (cache.TryGetValue(key, out var existing)) return existing;
        var material = new StandardMaterial3D { Roughness = 0.85f };
        if (part.Water)
        {
            material.ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded;
            material.Transparency = BaseMaterial3D.TransparencyEnum.Alpha;
            material.AlbedoColor = new Color(0.35f, 0.55f, 0.55f, 0.2f);
            material.DepthDrawMode = BaseMaterial3D.DepthDrawModeEnum.Disabled;
            material.CullMode = BaseMaterial3D.CullModeEnum.Disabled;
        }
        else
        {
            if (!part.DoubleSided) material.CullMode = BaseMaterial3D.CullModeEnum.Back;
            else material.CullMode = BaseMaterial3D.CullModeEnum.Disabled;
            if (part.Alpha == "mask")
            {
                material.Transparency = BaseMaterial3D.TransparencyEnum.AlphaScissor;
                material.AlphaScissorThreshold = (float)part.Cutoff;
            }
            else if (part.Alpha == "blend")
                material.Transparency = BaseMaterial3D.TransparencyEnum.Alpha;
            if (part.Texture.Length > 0 && textures.TryGetValue(part.Texture, out var image))
            {
                var texture = Upload(image, uploaded, report);
                if (texture is not null)
                {
                    material.AlbedoTexture = texture;
                    material.AlbedoTextureForceSrgb = image.Srgb;
                }
            }
            if (material.AlbedoTexture is null)
            {
                material.AlbedoColor = FlatColor(part.Material, part.Texture);
                material.Transparency = BaseMaterial3D.TransparencyEnum.Disabled;
            }
        }
        cache[key] = material;
        return material;
    }

    public static Image? ImageOf(CompressedTexture image, LoadReport? report)
    {
        var format = FormatOf(image);
        if (format is null || image.Blocks.Length == 0)
        {
            report?.AddReason($"{image.Name}: {image.Codec} is not uploaded");
            if (report is not null) report.TexturesFailed++;
            return null;
        }
        bool pixels = image.Codec == GpuCodec.Rgba8;
        int blockW = image.BlockW > 0 ? image.BlockW : pixels ? 1 : 4;
        int blockH = image.BlockH > 0 ? image.BlockH : blockW;
        int bytes = image.BytesPerBlock > 0 ? image.BytesPerBlock : pixels ? 4 : 16;
        int mip0 = MipChain.LevelBytes(image.Width, image.Height, blockW, blockH, bytes, pixels);
        if (image.Blocks.Length < mip0)
        {
            report?.AddReason($"{image.Name}: {image.Width}x{image.Height} {image.Codec} has {image.Blocks.Length} bytes, mip 0 needs {mip0}");
            if (report is not null) report.TexturesFailed++;
            return null;
        }
        bool chain = image.Blocks.Length > mip0;
        byte[] data = chain
            ? MipChain.Fit(image.Width, image.Height, blockW, blockH, bytes, pixels, image.Blocks)
            : image.Blocks.AsSpan(0, mip0).ToArray();
        int expected = chain ? MipChain.Expected(image.Width, image.Height, blockW, blockH, bytes, pixels) : mip0;
        if (data.Length != expected)
        {
            report?.AddReason($"{image.Name}: {image.Width}x{image.Height} prepared {data.Length} bytes, Godot wants {expected}");
            if (report is not null) report.TexturesFailed++;
            return null;
        }
        var picture = Image.CreateFromData(image.Width, image.Height, chain, format.Value, data);
        if (picture is null || picture.IsEmpty())
        {
            report?.AddReason($"{image.Name}: Image.CreateFromData rejected {image.Width}x{image.Height} {image.Codec}");
            if (report is not null) report.TexturesFailed++;
            return null;
        }
        if (report is not null) report.TexturesOk++;
        return picture;
    }

    public static Texture2D? Upload(CompressedTexture image, Dictionary<string, Texture2D> uploaded, LoadReport? report)
    {
        if (uploaded.TryGetValue(image.Name, out var existing)) return existing;
        var picture = ImageOf(image, report);
        if (picture is null) return null;
        var texture = ImageTexture.CreateFromImage(picture);
        uploaded[image.Name] = texture;
        return texture;
    }

    static Image.Format? FormatOf(CompressedTexture image) => image.Codec switch
    {
        GpuCodec.Bc7 => Image.Format.BptcRgba,
        GpuCodec.Bc1 => Image.Format.Dxt1,
        GpuCodec.Bc3 => Image.Format.Dxt5,
        GpuCodec.Bc4 => Image.Format.RgtcR,
        GpuCodec.Bc5 => Image.Format.RgtcRg,
        GpuCodec.Rgba8 => Image.Format.Rgba8,
        GpuCodec.Astc when image.BlockW == 4 && image.BlockH == 4 => Image.Format.Astc4X4,
        GpuCodec.Astc when image.BlockW == 8 && image.BlockH == 8 => Image.Format.Astc8X8,
        _ => null,
    };

    static Color FlatColor(string material, string texture)
    {
        var (r, g, b) = FlatColour.For(material, texture);
        return new Color(r, g, b);
    }

    static Material TerrainMaterial(ImportedField field)
    {
        var images = new Godot.Collections.Array<Image>();
        foreach (var layer in field.TerrainAlbedo)
        {
            var picture = ImageOf(layer, field.Report);
            if (picture is null)
            {
                images.Clear();
                break;
            }
            images.Add(picture);
        }
        if (images.Count > 0)
        {
            var array = new Texture2DArray();
            if (array.CreateFromImages(images) == Error.Ok)
            {
                var shader = new Shader { Code = TerrainShaderText.Code };
                var material = new ShaderMaterial { Shader = shader };
                material.SetShaderParameter("albedo", array);
                return material;
            }
            field.Report.AddReason("terrain texture array was rejected");
        }
        return new StandardMaterial3D
        {
            AlbedoColor = new Color(0.45f, 0.38f, 0.22f),
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
            Roughness = 1,
        };
    }

    static OccluderInstance3D Occluder(ImportedPart terrain)
    {
        int triangles = terrain.Indices.Count / 3;
        int stride = triangles > 20000 ? triangles / 20000 : 1;
        var vertices = new Vector3[terrain.Positions.Count];
        for (int i = 0; i < vertices.Length; i++)
        {
            var point = terrain.Positions[i];
            vertices[i] = new Vector3((float)point.X, (float)point.Y, (float)point.Z);
        }
        var indices = new List<int>(Math.Min(triangles, 20000) * 3);
        for (int tri = 0; tri < triangles; tri += stride)
        {
            indices.Add(terrain.Indices[tri * 3]);
            indices.Add(terrain.Indices[tri * 3 + 1]);
            indices.Add(terrain.Indices[tri * 3 + 2]);
        }
        var occluder = new ArrayOccluder3D();
        occluder.SetArrays(vertices, indices.ToArray());
        return new OccluderInstance3D { Name = "terrain-occluder", Occluder = occluder };
    }

    public static Transform3D ToTransform(double[] matrix) => new(
        new Basis(
            new Vector3((float)matrix[0], (float)matrix[4], (float)matrix[8]),
            new Vector3((float)matrix[1], (float)matrix[5], (float)matrix[9]),
            new Vector3((float)matrix[2], (float)matrix[6], (float)matrix[10])),
        new Vector3((float)matrix[3], (float)matrix[7], (float)matrix[11]));
}
