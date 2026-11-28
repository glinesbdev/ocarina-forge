using Godot;
using OcarinaForge.Import;
using OcarinaForge.Project;

namespace OcarinaForge.Generated;

/// <summary>Writes native Godot resources from a dump. The CLI and the editor plugin both call <see cref="Generate"/>.</summary>
public static class ProjectEmitter
{
    public static int Generate()
    {
        string projectDir = ProjectSettings.GlobalizePath("res://");
        try
        {
            var request = ManifestStore.LoadRequest(projectDir);
            var dump = DumpFolder.Open(request.Dump);
            var field = FieldLoader.Load(dump, request.Banc);
            AstcTranscode.Apply(field);
            var existing = ManifestStore.Load(projectDir);
            var job = ProjectPlan.Build(dump, field, existing, projectDir);
            int texturesWritten = 0;
            int texturesSkipped = 0;
            foreach (var texture in job.Textures)
            {
                if (texture.Current)
                {
                    texturesSkipped++;
                    GD.Print($"generate texture {texture.Name} skipped");
                    continue;
                }
                if (SaveTexture(texture.Image, texture.ResPath, field.Report) is null)
                    throw new InvalidOperationException($"could not write {texture.ResPath}");
                texturesWritten++;
                GD.Print($"generate texture {texture.Name} written");
            }
            int modelsWritten = 0;
            int modelsSkipped = 0;
            if (job.Map.HasWater) WriteWater();
            foreach (var model in job.Models)
            {
                if (model.Current)
                {
                    modelsSkipped++;
                    GD.Print($"generate model {model.Gyaml} skipped");
                    continue;
                }
                WriteModel(model);
                modelsWritten++;
                GD.Print($"generate model {model.Gyaml} written");
            }
            if (job.Map.Current) GD.Print("generate map skipped");
            else
            {
                WriteMap(job);
                GD.Print("generate map written");
            }
            var manifest = ProjectPlan.ToManifest(job);
            manifest.Summary.TexturesOk = texturesWritten + texturesSkipped;
            manifest.Summary.Bc7Bytes = field.Bc7Bytes;
            ManifestStore.Save(projectDir, manifest);
            GD.Print($"generate models {job.Models.Count} written {modelsWritten} skipped {modelsSkipped}");
            GD.Print($"generate textures {job.Textures.Count} written {texturesWritten} skipped {texturesSkipped}");
            GD.Print(field.Report.Format());
            GD.Print("generate done");
            return 0;
        }
        catch (Exception exc)
        {
            GD.PrintErr(exc);
            GD.Print("generate failed");
            return 1;
        }
    }

    public static int Check()
    {
        if (!ResourceLoader.Exists(ProjectPaths.MapScene))
        {
            GD.PrintErr("check map missing");
            return 1;
        }
        var map = ResourceLoader.Load<PackedScene>(ProjectPaths.MapScene).Instantiate<Node3D>();
        int actors = map.GetNodeOrNull("Actors")?.GetChildCount() ?? 0;
        int meshes = Count<MeshInstance3D>(map);
        GD.Print($"check actors {actors}");
        GD.Print($"check meshes {meshes}");
        string modelPath = FirstModel();
        if (modelPath.Length == 0)
        {
            GD.PrintErr("check model missing");
            return 1;
        }
        var model = ResourceLoader.Load<PackedScene>(modelPath).Instantiate<Node3D>();
        var skeleton = Find<Skeleton3D>(model);
        var player = Find<AnimationPlayer>(model);
        GD.Print($"check skeleton {skeleton?.GetBoneCount() ?? -1}");
        GD.Print(player is null ? "check animation missing" : "check animation player");
        GD.Print("check curves not imported");
        string manifest = Path.Combine(ProjectSettings.GlobalizePath("res://"), ".ocarina", "manifest.json");
        GD.Print(File.Exists(manifest) ? "check manifest" : "check manifest missing");
        map.Free();
        model.Free();
        if (actors <= 0 || meshes <= 0 || player is null || skeleton is null || !File.Exists(manifest)) return 1;
        GD.Print("check ok");
        return 0;
    }

    static void WriteModel(ModelJob job)
    {
        foreach (var part in job.Model.Parts.Where(ProjectPaths.Drawn))
            WriteMaterial(job, part);
        bool hasFar = job.Model.Parts.Any(part => part.Lod > 0 && ProjectPaths.Drawn(part));
        var root = new Node3D { Name = ProjectPaths.Safe(job.Gyaml) };
        root.SetMeta("ocarina_source", job.ModelPath);
        root.SetMeta("ocarina_animation", "FSKA curves are not imported. Skeleton3D is the bind pose. AnimationPlayer has an empty library.");
        AddSkeleton(root, job.Model.Skeleton);
        var player = new AnimationPlayer { Name = "AnimationPlayer" };
        player.AddAnimationLibrary("", new AnimationLibrary());
        root.AddChild(player);
        var used = new HashSet<string>(StringComparer.Ordinal);
        foreach (var part in job.Model.Parts.Where(ProjectPaths.Drawn))
        {
            string meshPath = ProjectPaths.Mesh(job.Gyaml, part.Name, part.Lod);
            if (used.Add(meshPath)) Save(MakeMesh(part), meshPath);
            string name = NodeName(part.Name, part.Lod);
            string nodeName = name;
            int n = 2;
            while (!used.Add("node:" + nodeName)) nodeName = name + "_" + n++;
            var body = new MeshInstance3D
            {
                Name = nodeName,
                Mesh = Load<ArrayMesh>(meshPath),
                MaterialOverride = Load<Material>(MaterialPath(job, part)),
                CastShadow = GeometryInstance3D.ShadowCastingSetting.Off,
            };
            ApplyRange(body, part, hasFar);
            root.AddChild(body);
        }
        Own(root);
        var packed = new PackedScene();
        if (packed.Pack(root) != Error.Ok) throw new InvalidOperationException($"could not pack {job.Gyaml}");
        Save(packed, ProjectPaths.ModelScene(job.Gyaml));
        root.Free();
    }

    static void WriteMaterial(ModelJob job, ImportedPart part)
    {
        string path = MaterialPath(job, part);
        if (part.Water) return;
        var material = new StandardMaterial3D { Roughness = 0.85f, ResourceName = ProjectPaths.Safe(ProjectPlan.MaterialKey(part)) };
        if (!part.DoubleSided) material.CullMode = BaseMaterial3D.CullModeEnum.Back;
        else material.CullMode = BaseMaterial3D.CullModeEnum.Disabled;
        if (part.Alpha == "mask")
        {
            material.Transparency = BaseMaterial3D.TransparencyEnum.AlphaScissor;
            material.AlphaScissorThreshold = (float)part.Cutoff;
        }
        else if (part.Alpha == "blend")
            material.Transparency = BaseMaterial3D.TransparencyEnum.Alpha;
        if (part.Texture.Length > 0 && job.TexturePaths.TryGetValue(part.Texture, out var texturePath) && ResourceLoader.Exists(texturePath))
        {
            material.AlbedoTexture = Load<Texture2D>(texturePath);
            if (job.Textures.TryGetValue(part.Texture, out var image))
                material.AlbedoTextureForceSrgb = image.Srgb;
        }
        if (material.AlbedoTexture is null)
        {
            var (r, g, b) = FlatColour.For(part.Material, part.Texture);
            material.AlbedoColor = new Color(r, g, b);
            material.Transparency = BaseMaterial3D.TransparencyEnum.Disabled;
        }
        Save(material, path);
    }

    static string MaterialPath(ModelJob job, ImportedPart part) =>
        part.Water ? ProjectPaths.WaterMaterial : job.MaterialPaths[ProjectPlan.MaterialKey(part)];

    static void WriteWater()
    {
        if (ResourceLoader.Exists(ProjectPaths.WaterMaterial)) return;
        var material = new StandardMaterial3D
        {
            ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded,
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            AlbedoColor = new Color(0.35f, 0.55f, 0.55f, 0.2f),
            DepthDrawMode = BaseMaterial3D.DepthDrawModeEnum.Disabled,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
            Roughness = 0.85f,
        };
        Save(material, ProjectPaths.WaterMaterial);
    }

    static void WriteMap(ProjectJob job)
    {
        var map = new Node3D { Name = "HyruleField" };
        map.SetMeta("ocarina_banc", job.Banc);
        AddRig(map);
        if (job.Map.HasTerrain && job.Field.Terrain is { } terrain)
            AddTerrain(map, job.Field, terrain);
        var actors = new Node3D { Name = "Actors" };
        map.AddChild(actors);
        var names = new HashSet<string>(StringComparer.Ordinal);
        foreach (var model in job.Models)
        {
            string scenePath = ProjectPaths.ModelScene(model.Gyaml);
            if (!ResourceLoader.Exists(scenePath)) continue;
            var packed = Load<PackedScene>(scenePath);
            foreach (var actor in model.Actors)
            {
                var node = packed.Instantiate<Node3D>();
                string name = $"{ProjectPaths.Safe(model.Gyaml)}_{actor.Placement.Hash:x}";
                if (!names.Add(name)) name += "_" + names.Count;
                node.Name = name;
                node.Transform = ToTransform(actor.Placement.Matrix());
                actors.AddChild(node);
                node.Owner = map;
            }
        }
        Own(map);
        var scene = new PackedScene();
        if (scene.Pack(map) != Error.Ok) throw new InvalidOperationException("could not pack Hyrule Field");
        Save(scene, ProjectPaths.MapScene);
        map.Free();
    }

    static void AddTerrain(Node3D map, ImportedField field, ImportedPart terrain)
    {
        string disk = ProjectSettings.GlobalizePath(ProjectPaths.TerrainShader);
        Directory.CreateDirectory(Path.GetDirectoryName(disk)!);
        File.WriteAllText(disk, TerrainShaderText.Code);
        Save(MakeMesh(terrain), ProjectPaths.TerrainMesh);
        Material material = TerrainMaterial(field);
        Save(material, ProjectPaths.TerrainMaterial);
        var body = new MeshInstance3D
        {
            Name = "Terrain",
            Mesh = Load<ArrayMesh>(ProjectPaths.TerrainMesh),
            MaterialOverride = Load<Material>(ProjectPaths.TerrainMaterial),
            CastShadow = GeometryInstance3D.ShadowCastingSetting.Off,
        };
        map.AddChild(body);
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
        Save(occluder, ProjectPaths.TerrainOccluder);
        map.AddChild(new OccluderInstance3D { Name = "TerrainOccluder", Occluder = Load<ArrayOccluder3D>(ProjectPaths.TerrainOccluder) });
    }

    static Material TerrainMaterial(ImportedField field)
    {
        var images = new Godot.Collections.Array<Image>();
        for (int i = 0; i < field.TerrainAlbedo.Count; i++)
        {
            var picture = Picture(field.TerrainAlbedo[i], field.Report);
            if (picture is null)
            {
                images.Clear();
                break;
            }
            images.Add(picture);
            var layer = ImageTexture.CreateFromImage(picture);
            Save(layer, ProjectPaths.TerrainLayer(i));
        }
        if (images.Count > 0)
        {
            var array = new Texture2DArray();
            if (array.CreateFromImages(images) == Error.Ok)
            {
                Save(array, ProjectPaths.TerrainArray);
                var shader = ResourceLoader.Load<Shader>(ProjectPaths.TerrainShader, cacheMode: ResourceLoader.CacheMode.Replace);
                if (shader is null) shader = new Shader { Code = TerrainShaderText.Code };
                var material = new ShaderMaterial { Shader = shader };
                material.SetShaderParameter("albedo", Load<Texture2DArray>(ProjectPaths.TerrainArray));
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

    static void AddRig(Node3D root)
    {
        var environment = new Godot.Environment
        {
            BackgroundMode = Godot.Environment.BGMode.Color,
            BackgroundColor = new Color(EditorRig.BackgroundR, EditorRig.BackgroundG, EditorRig.BackgroundB),
            AmbientLightSource = Godot.Environment.AmbientSource.Color,
            AmbientLightColor = new Color(EditorRig.AmbientR, EditorRig.AmbientG, EditorRig.AmbientB),
            AmbientLightEnergy = EditorRig.AmbientEnergy,
            TonemapMode = Godot.Environment.ToneMapper.Aces,
            TonemapExposure = EditorRig.Exposure,
        };
        root.AddChild(new WorldEnvironment { Name = "WorldEnvironment", Environment = environment });
        var sun = new DirectionalLight3D
        {
            Name = "Sun",
            LightColor = new Color(EditorRig.SunR, EditorRig.SunG, EditorRig.SunB),
            LightEnergy = EditorRig.SunEnergy,
            ShadowEnabled = false,
        };
        sun.LookAtFromPosition(new Vector3(EditorRig.SunX, EditorRig.SunY, EditorRig.SunZ), Vector3.Zero, Vector3.Up);
        root.AddChild(sun);
    }

    static void AddSkeleton(Node3D root, List<ImportedBone> bones)
    {
        var skeleton = new Skeleton3D { Name = "Skeleton" };
        var used = new HashSet<string>(StringComparer.Ordinal);
        foreach (var bone in bones)
        {
            string unique = ProjectPaths.Safe(bone.Name);
            if (unique.Length == 0) unique = "bone";
            string candidate = unique;
            int n = 2;
            while (!used.Add(candidate)) candidate = unique + "_" + n++;
            skeleton.AddBone(candidate);
        }
        for (int i = 0; i < bones.Count; i++)
        {
            var bone = bones[i];
            if (bone.Parent >= 0 && bone.Parent < bones.Count && bone.Parent != i)
                skeleton.SetBoneParent(i, bone.Parent);
            var quat = new Quaternion((float)bone.Rx, (float)bone.Ry, (float)bone.Rz, (float)bone.Rw);
            if (quat.LengthSquared() < 1e-8f) quat = Quaternion.Identity;
            else quat = quat.Normalized();
            var scale = new Vector3((float)bone.Sx, (float)bone.Sy, (float)bone.Sz);
            var rest = new Transform3D(new Basis(quat).Scaled(scale), new Vector3((float)bone.Px, (float)bone.Py, (float)bone.Pz));
            skeleton.SetBoneRest(i, rest);
            skeleton.SetBonePosePosition(i, rest.Origin);
            skeleton.SetBonePoseRotation(i, quat);
            skeleton.SetBonePoseScale(i, scale);
        }
        root.AddChild(skeleton);
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
        arrays[(int)Mesh.ArrayType.Index] = part.Indices.ToArray();
        if (uvs is not null) arrays[(int)Mesh.ArrayType.TexUV] = uvs;
        if (uv2 is not null) arrays[(int)Mesh.ArrayType.TexUV2] = uv2;
        if (colors is not null) arrays[(int)Mesh.ArrayType.Color] = colors;
        if (normals is not null) arrays[(int)Mesh.ArrayType.Normal] = normals;
        var mesh = new ArrayMesh();
        mesh.AddSurfaceFromArrays(Mesh.PrimitiveType.Triangles, arrays);
        return mesh;
    }

    static Texture2D? SaveTexture(CompressedTexture image, string resPath, LoadReport report)
    {
        var picture = Picture(image, report);
        if (picture is null) return null;
        var texture = ImageTexture.CreateFromImage(picture);
        texture.ResourceName = image.Name;
        Save(texture, resPath);
        return Load<Texture2D>(resPath);
    }

    static Image? Picture(CompressedTexture image, LoadReport report)
    {
        var format = image.Codec switch
        {
            GpuCodec.Bc7 => Image.Format.BptcRgba,
            GpuCodec.Bc1 => Image.Format.Dxt1,
            GpuCodec.Bc3 => Image.Format.Dxt5,
            GpuCodec.Bc4 => Image.Format.RgtcR,
            GpuCodec.Bc5 => Image.Format.RgtcRg,
            GpuCodec.Rgba8 => Image.Format.Rgba8,
            GpuCodec.Astc when image.BlockW == 4 && image.BlockH == 4 => Image.Format.Astc4X4,
            GpuCodec.Astc when image.BlockW == 8 && image.BlockH == 8 => Image.Format.Astc8X8,
            _ => (Image.Format?)null,
        };
        if (format is null || image.Blocks.Length == 0)
        {
            report.AddReason($"{image.Name}: {image.Codec} is not uploaded");
            report.TexturesFailed++;
            return null;
        }
        bool pixels = image.Codec == GpuCodec.Rgba8;
        int blockW = image.BlockW > 0 ? image.BlockW : pixels ? 1 : 4;
        int blockH = image.BlockH > 0 ? image.BlockH : blockW;
        int bytes = image.BytesPerBlock > 0 ? image.BytesPerBlock : pixels ? 4 : 16;
        int mip0 = MipChain.LevelBytes(image.Width, image.Height, blockW, blockH, bytes, pixels);
        if (image.Blocks.Length < mip0) return null;
        bool chain = image.Blocks.Length > mip0;
        byte[] data = chain
            ? MipChain.Fit(image.Width, image.Height, blockW, blockH, bytes, pixels, image.Blocks)
            : image.Blocks.AsSpan(0, mip0).ToArray();
        var picture = Image.CreateFromData(image.Width, image.Height, chain, format.Value, data);
        if (picture is null || picture.IsEmpty()) return null;
        return picture;
    }

    static void Save(Resource resource, string resPath)
    {
        string disk = ProjectSettings.GlobalizePath(resPath);
        Directory.CreateDirectory(Path.GetDirectoryName(disk)!);
        Error err = ResourceSaver.Save(resource, resPath);
        if (err != Error.Ok) throw new InvalidOperationException($"save {resPath} returned {err}");
    }

    static T Load<T>(string resPath) where T : Resource
    {
        var loaded = ResourceLoader.Load<T>(resPath, cacheMode: ResourceLoader.CacheMode.Replace);
        if (loaded is null) throw new InvalidOperationException($"could not load {resPath}");
        return loaded;
    }

    static void Own(Node root)
    {
        var stack = new Stack<Node>();
        stack.Push(root);
        while (stack.Count > 0)
        {
            var node = stack.Pop();
            foreach (var child in node.GetChildren())
            {
                if (child.Owner is null) child.Owner = root;
                stack.Push(child);
            }
        }
    }

    static Transform3D ToTransform(double[] matrix) => new(
        new Basis(
            new Vector3((float)matrix[0], (float)matrix[4], (float)matrix[8]),
            new Vector3((float)matrix[1], (float)matrix[5], (float)matrix[9]),
            new Vector3((float)matrix[2], (float)matrix[6], (float)matrix[10])),
        new Vector3((float)matrix[3], (float)matrix[7], (float)matrix[11]));

    static string NodeName(string name, int lod)
    {
        string safe = ProjectPaths.Safe(name);
        if (safe.Length == 0) safe = "part";
        return lod == 0 ? safe : safe + "_lod" + lod;
    }

    static string FirstModel()
    {
        var dir = DirAccess.Open("res://models");
        if (dir is null) return "";
        dir.ListDirBegin();
        while (true)
        {
            string folder = dir.GetNext();
            if (folder.Length == 0) return "";
            if (!dir.CurrentIsDir() || folder.StartsWith('.')) continue;
            string scene = $"res://models/{folder}/{folder}.tscn";
            if (ResourceLoader.Exists(scene)) return scene;
        }
    }

    static int Count<T>(Node node) where T : Node
    {
        int count = node is T ? 1 : 0;
        foreach (var child in node.GetChildren()) count += Count<T>(child);
        return count;
    }

    static T? Find<T>(Node node) where T : Node
    {
        if (node is T found) return found;
        foreach (var child in node.GetChildren())
        {
            var nested = Find<T>(child);
            if (nested is not null) return nested;
        }
        return null;
    }
}
