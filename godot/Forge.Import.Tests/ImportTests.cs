using System.Text.Json;
using Xunit;

namespace OcarinaForge.Import.Tests;

public class ImportTests
{
    static string Fixture(string name) => Path.Combine(AppContext.BaseDirectory, "Fixtures", name);

    [Fact]
    public void V10TreeMatchesPythonFixture()
    {
        var models = BfresReader.Read(File.ReadAllBytes(Fixture("v10-tree.bfres")));
        var expected = JsonSerializer.Deserialize<List<ExpectedModel>>(File.ReadAllText(Fixture("v10-tree.expected.json")), JsonOptions)!;
        Assert.Equal(expected[0].Name, models[0].Name);
        Assert.Empty(models[0].Notes);
        Assert.Equal(2, models[0].Parts.Count);
        for (int i = 0; i < 2; i++)
        {
            var part = models[0].Parts[i];
            var want = expected[0].Parts[i];
            Assert.Equal(want.Name, part.Name);
            Assert.Equal(want.Material, part.Material);
            Assert.Equal(want.Texture, part.Texture);
            Assert.Equal(want.AlbedoSampler, part.AlbedoSampler);
            Assert.Equal(want.Alpha, part.Alpha);
            Assert.Equal(want.DoubleSided, part.DoubleSided);
            Assert.Equal(want.Indices, part.Indices);
            Assert.Equal(want.Positions.Select(p => (p[0], p[1], p[2])), part.Positions.Select(p => (p.X, p.Y, p.Z)));
            Assert.Equal(want.Binds.Select(b => (b[0], b[1])), part.Binds);
        }
    }

    [Fact]
    public void MeshletMatchesPythonFixture()
    {
        var models = BfresReader.Read(File.ReadAllBytes(Fixture("meshlet.bfres")));
        var part = Assert.Single(models[0].Parts);
        var expected = JsonSerializer.Deserialize<JsonElement>(File.ReadAllText(Fixture("meshlet.expected.json")));
        var positions = expected.GetProperty("positions").EnumerateArray().Select(item => (item[0].GetDouble(), item[1].GetDouble(), item[2].GetDouble())).ToList();
        Assert.Equal(positions, part.Positions.Select(p => (p.X, p.Y, p.Z)));
        Assert.Equal(expected.GetProperty("indices").EnumerateArray().Select(item => item.GetInt32()), part.Indices);
        var normal = part.Normals![0];
        Assert.Equal(0, normal.X, 4);
        Assert.Equal(0, normal.Y, 4);
        Assert.Equal(-1, normal.Z, 4);
        Assert.Equal((0, 0), (part.Uvs![0].U, part.Uvs[0].V));
        Assert.Equal((0.5, 0), (part.Uvs[1].U, part.Uvs[1].V));
    }

    [Fact]
    public void BancRoundTripReadsHashAndYaw()
    {
        var document = Byml.Load(File.ReadAllBytes(Fixture("banc.bcett.byml")));
        var actors = ActorList.Read(document, "static");
        var actor = Assert.Single(actors);
        Assert.Equal("Obj_Tree", actor.Gyaml);
        Assert.Equal(0x11UL, actor.Hash);
        Assert.Equal(1.5, actor.X);
        Assert.Equal(10, actor.Y);
        Assert.Equal(-3, actor.Z);
        Assert.Equal(Math.PI / 2, actor.Ry, 5);
        var moved = BindPose.Apply(actor.Matrix(), 1, 0, 0);
        Assert.Equal(1.5, moved.X, 5);
        Assert.Equal(10, moved.Y, 5);
        Assert.Equal(-4, moved.Z, 5);
    }

    [Fact]
    public void YawTakesLocalXToWorldMinusZ()
    {
        var matrix = BindPose.Matrix(0, 0, 0, 0, Math.PI / 2, 0, 1, 1, 1);
        var point = BindPose.Apply(matrix, 1, 0, 0);
        Assert.Equal(0, point.X, 5);
        Assert.Equal(0, point.Y, 5);
        Assert.Equal(-1, point.Z, 5);
    }

    [Fact]
    public void Bc7StaysCompressed()
    {
        var image = Assert.Single(BntxReader.Read(SyntheticDump.Bark()));
        Assert.Equal(GpuCodec.Bc7, image.Codec);
        Assert.True(image.Srgb);
        Assert.Equal(64, image.Blocks.Length);
        Assert.NotEqual(8 * 8 * 4, image.Blocks.Length);
    }

    [Fact]
    public void Switch2GobOrderRoundTripsSixteenByteBlocks()
    {
        var linear = new byte[2 * 2 * 16];
        for (int i = 0; i < linear.Length; i++) linear[i] = (byte)(i * 17);
        var swizzled = BlockLinear.Swizzle(8, 8, 4, 4, 16, 0, 1, linear, BlockLinear.Switch2);
        var back = BlockLinear.Deswizzle(8, 8, 4, 4, 16, 0, 1, swizzled, BlockLinear.Switch2);
        Assert.Equal(linear, back);
        Assert.NotEqual(linear, swizzled);
    }

    [Fact]
    public void SarcRoundTrip()
    {
        var packed = SarcArchive.Pack(new Dictionary<string, byte[]> { ["a.txt"] = "hello"u8.ToArray(), ["b/c.bin"] = new byte[] { 1, 2, 3 } });
        var archive = new SarcArchive(packed);
        Assert.Equal("hello"u8.ToArray(), archive.Read("a.txt"));
        Assert.Equal(new byte[] { 1, 2, 3 }, archive.Read("b/c.bin"));
    }

    [Fact]
    public void FlightStaysAtOriginForZeroDtAndFinishesTheLoop()
    {
        var flight = new FieldFlight();
        var pose = flight.Sample(0);
        Assert.Equal(0, pose.X);
        Assert.Equal(190, pose.Y);
        Assert.Equal(0, pose.Z);
        Assert.Equal(150, pose.LookY);
        Assert.False(flight.Done);
        int frames = 0;
        while (!flight.Done && frames < 100000)
        {
            flight.Sample(FieldFlight.FrameDt);
            frames++;
        }
        Assert.True(flight.Done);
        var end = flight.Sample(FieldFlight.FrameDt);
        Assert.Equal(0, end.X, 4);
        Assert.Equal(0, end.Z, 4);
    }

    [Fact]
    public void SyntheticFieldLoadsTerrainActorsAndBc7()
    {
        string root = SyntheticDump.Write(Path.Combine(Path.GetTempPath(), "of-synth-" + Guid.NewGuid().ToString("N")));
        try
        {
            var field = FieldLoader.Load(DumpFolder.Open(root));
            Assert.Equal(3, field.Actors.Count(actor => actor.Model is not null));
            var model = field.Actors[0].Model!;
            Assert.Equal(2, model.Parts.Count);
            Assert.True(field.Actors[0].Textures.ContainsKey("Bark_Alb"));
            Assert.Equal(64, field.Actors[0].Textures["Bark_Alb"].Blocks.Length);
            Assert.Equal(64, field.Bc7Bytes);
            Assert.NotNull(field.Terrain);
            Assert.InRange(field.Terrain!.Positions.Max(point => point.Y), 49.9, 50.1);
            string cache = Path.Combine(root, "cache", "field.ofm");
            ImportCache.Save(cache, field);
            var loaded = ImportCache.TryLoad(cache);
            Assert.NotNull(loaded);
            Assert.Equal(field.Actors.Count, loaded!.Actors.Count);
            Assert.Equal(field.Terrain.Positions.Count, loaded.Terrain!.Positions.Count);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void GodotMipChainCountsTheTailBlock()
    {
        Assert.Equal(158416, MipChain.Expected(341, 341, 4, 4, 16, false));
        Assert.Equal(174776, MipChain.Expected(512, 512, 4, 4, 8, false));
        Assert.Equal(20040, MipChain.Expected(171, 171, 4, 4, 8, false));
        var shortChain = new byte[158400];
        Assert.Equal(158416, MipChain.Fit(341, 341, 4, 4, 16, false, shortChain).Length);
    }

    [Fact]
    public void MipReaderKeepsTheLevelAfterTheBlockSizedOne()
    {
        byte[] level0 = new byte[64];
        byte[] level1 = new byte[16];
        byte[] level2 = new byte[16];
        level2[0] = 0xAB;
        var file = BntxReader.WriteLinearMips("Tail", 8, 8, 0x2001, new[] { level0, level1, level2 });
        var image = Assert.Single(BntxReader.Read(file));
        Assert.Equal(64 + 16 + 16, image.Blocks.Length);
        Assert.Equal(0xAB, image.Blocks[^16]);
    }

    [Fact]
    public void BoneIndexIsTheRawByte()
    {
        Assert.Equal(200, BfresReader.PaletteSlot(new byte[] { 200, 1, 2, 3 }, 0x010B, 0));
        Assert.Equal(255, BfresReader.PaletteSlot(new byte[] { 255, 0, 0, 0 }, 0x020B, 0));
        Assert.Equal(200, BfresReader.PaletteSlot(new byte[] { 200 }, 0x0302, 0));
        Assert.Equal(200, BfresReader.PaletteSlot(new byte[] { 200, 1 }, 0x0309, 0));
        Assert.Equal(200, BfresReader.PaletteSlot(new byte[] { 200, 1, 2, 3 }, 0x030B, 0));
    }

    [Fact]
    public void YawBoneBakesLocalXToWorldMinusZAndTheNormalFollows()
    {
        var turn = BindPose.Euler(0, Math.PI / 2, 0);
        var bones = new List<BindPose.Bone>
        {
            new(-1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0),
            new(0, 0, 0, 0, turn.X, turn.Y, turn.Z, turn.W, 1, 1, 1, 0x1000, 0),
            new(0, 4, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, -1),
        };
        var point = new List<(double X, double Y, double Z)> { (1, 0, 0) };
        BindPose.Bake(point, bones, Array.Empty<int>(), 1, null);
        Assert.InRange(point[0].X, -1e-4, 1e-4);
        Assert.InRange(point[0].Z, -1 - 1e-4, -1 + 1e-4);
        var placed = new List<(double X, double Y, double Z)> { (1, 0, 0), (0, 1, 0) };
        BindPose.Bake(placed, bones, new[] { 1, 2 }, 0, new[] { 0, 1 });
        Assert.InRange(placed[0].Z, -1 - 1e-4, -1 + 1e-4);
        Assert.InRange(placed[1].X, -1e-4, 1e-4);
        Assert.InRange(placed[1].Y, 1 - 1e-4, 1 + 1e-4);
        var quadRaw = new List<(double X, double Y, double Z)> { (0, 0, 0), (0, 1, 0), (0, 0, 1) };
        BindPose.Bake(quadRaw, bones, Array.Empty<int>(), 1, null);
        var quad = quadRaw.Select(p => new Vec3(p.X, p.Y, p.Z)).ToList();
        var normal = MeshNormals.Generate(quad, new List<int> { 0, 1, 2 })[0];
        Assert.InRange(normal.Z, -1 - 1e-4, -1 + 1e-4);
        Assert.InRange(normal.X, -1e-4, 1e-4);
    }

    [Fact]
    public void VolumesAreQuietAndPlantsAreNot()
    {
        Assert.Contains("volume", ActorList.SkipReason("ForbidAutoPlacementAreaBox"));
        Assert.Contains("volume", ActorList.SkipReason("VolumeFogArea"));
        Assert.Contains("marker", ActorList.SkipReason("Land_HrlField_Airwall_Outer_Boundary"));
        Assert.Contains("marker", ActorList.SkipReason("Obj_Gimmick_AnswerPosAnchor"));
        Assert.Equal("", ActorList.SkipReason("Land_Graveyard_Plants_A_08"));
    }

    [Fact]
    public void PackStringNamesTheModelFile()
    {
        string root = Path.Combine(Path.GetTempPath(), "of-model-" + Guid.NewGuid().ToString("N"));
        string romfs = Path.Combine(root, "romfs");
        Directory.CreateDirectory(Path.Combine(romfs, "Pack", "Actor"));
        Directory.CreateDirectory(Path.Combine(romfs, "Model"));
        File.WriteAllBytes(Path.Combine(romfs, "Model", "Plants.Plants_A.bfres"), File.ReadAllBytes(Fixture("v10-tree.bfres")));
        var pack = SarcArchive.Pack(new Dictionary<string, byte[]>
        {
            ["note.txt"] = System.Text.Encoding.ASCII.GetBytes("see Model/Plants.Plants_A.bfres please"),
        });
        File.WriteAllBytes(Path.Combine(romfs, "Pack", "Actor", "Land_Plant.pack"), pack);
        try
        {
            var found = ModelLocator.Find(DumpFolder.Open(root), "Land_Plant", new List<string>());
            Assert.NotNull(found);
            Assert.EndsWith("Model/Plants.Plants_A.bfres", found!.Path.Replace('\\', '/'));
            Assert.NotEmpty(found.Model.Parts);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void MateGridPicksMaterialAUnlessBlendIsHigh()
    {
        const int tight = 257 * 257 * 4;
        var payload = new byte[0x40 + tight];
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(0x2C), 0x14);
        System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(0x30), tight);
        for (int i = 0; i < 257 * 257; i++)
        {
            payload[0x40 + i * 4] = 3;
            payload[0x40 + i * 4 + 1] = 9;
            payload[0x40 + i * 4 + 2] = 0;
        }
        payload[0x40 + 2] = 200;
        var layers = TerrainMesh.ReadMate(payload, null);
        Assert.NotNull(layers);
        Assert.Equal(9, layers![0]);
        Assert.Equal(3, layers[1]);
        payload[0x40 + 2] = 0x7F;
        var halfway = TerrainMesh.ReadMateGrid(payload, null);
        Assert.NotNull(halfway);
        Assert.Equal(3, halfway!.Chosen[0]);
        Assert.Equal(9, halfway.Other[0]);
        Assert.InRange(halfway.Weight[0], 0.49f, 0.51f);
        Assert.Equal(200f / 255f, TerrainMesh.PairWeight(200, 0), 3);
    }

    [Fact]
    public void HelpersAreSkippedAndPlantsStay()
    {
        Assert.Contains("marker", ActorList.SkipReason("LookAttentionActor_HidingHoleEntrance"));
        Assert.Contains("marker", ActorList.SkipReason("ShowToCameraPoint"));
        Assert.Contains("marker", ActorList.SkipReason("Obj_HorseJumpForbidHeight"));
        Assert.Equal("", ActorList.SkipReason("Land_Graveyard_Plants_A_08"));
        Assert.Equal("", ActorList.SkipReason("Land_kokiri_Plant_A_02_DropFixed_03"));
        Assert.Contains("Land_kokiri_Plant_A_02", ModelLocator.PlacementStems("Land_kokiri_Plant_A_02_DropFixed_03"));
        Assert.Contains("Land_Graveyard_Plants_A", ModelLocator.PlacementStems("Land_Graveyard_Plants_A_08"));
        Assert.Contains("Land_Graveyard_Plants", ModelLocator.PlacementStems("Land_Graveyard_Plants_A_08"));
        Assert.Contains("Land_LonLon_Plant_A", ModelLocator.PlacementStems("Land_LonLon_Plant_A_01"));
        Assert.Contains("Land_LonLon_Plant", ModelLocator.PlacementStems("Land_LonLon_Plant_A_01"));
        Assert.Contains("marker", ActorList.SkipReason("FairySpirit_AppearPoint_SunSong"));
        Assert.Contains("marker", ActorList.SkipReason("MiniGame_Supporter"));
        Assert.Contains("marker", ActorList.SkipReason("HrlField_Note_15"));
    }

    [Fact]
    public void DropFixedPlantUsesTheUnsuffixedFile()
    {
        string root = Path.Combine(Path.GetTempPath(), "of-plant-" + Guid.NewGuid().ToString("N"));
        string romfs = Path.Combine(root, "romfs");
        Directory.CreateDirectory(Path.Combine(romfs, "Model"));
        File.WriteAllBytes(Path.Combine(romfs, "Model", "Land_kokiri_Plant_A_02.Land_kokiri_Plant_A_02.bfres"), File.ReadAllBytes(Fixture("v10-tree.bfres")));
        try
        {
            var found = ModelLocator.Find(DumpFolder.Open(root), "Land_kokiri_Plant_A_02_DropFixed_03", new List<string>());
            Assert.NotNull(found);
            Assert.EndsWith("Model/Land_kokiri_Plant_A_02.Land_kokiri_Plant_A_02.bfres", found!.Path.Replace('\\', '/'));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void BoundsBoxIsNotDrawn()
    {
        var box = new ImportedPart
        {
            Name = "bounds",
            Positions = Enumerable.Range(0, 8).Select(i => new Vec3(i, 0, 0)).ToList(),
        };
        var wall = new ImportedPart
        {
            Name = "wall",
            Positions = Enumerable.Range(0, 30).Select(i => new Vec3(i * 0.1, 1, 0)).ToList(),
            Uvs = Enumerable.Range(0, 30).Select(_ => new Vec2(0, 0)).ToList(),
        };
        var parts = new List<ImportedPart> { box, wall };
        ShapeFilter.Hide(parts);
        Assert.Contains("bounds", box.OmitReason);
        Assert.Equal("", wall.OmitReason);
        var slab = new ImportedPart
        {
            Name = "pPlane1",
            Material = "Mt_FieldSlab",
            Positions = Enumerable.Range(0, 40).Select(i => new Vec3(i * 0.5, 0, (i % 2) * 0.02)).ToList(),
        };
        var kept = new ImportedPart
        {
            Name = "cliff",
            Positions = Enumerable.Range(0, 12).Select(i => new Vec3(i, i, 1)).ToList(),
            Uvs = Enumerable.Range(0, 12).Select(_ => new Vec2(0, 0)).ToList(),
        };
        var field = new List<ImportedPart> { slab, kept };
        ShapeFilter.Hide(field);
        Assert.Contains("slab", slab.OmitReason);
        Assert.Contains("pPlane1", slab.OmitReason);
        Assert.Equal("", kept.OmitReason);
    }

    [Fact]
    public void SharedCliffAlbedoIsFoundInsideAShorterBntx()
    {
        string root = Path.Combine(Path.GetTempPath(), "of-tex-" + Guid.NewGuid().ToString("N"));
        string romfs = Path.Combine(root, "romfs", "Tex");
        Directory.CreateDirectory(romfs);
        File.WriteAllBytes(
            Path.Combine(romfs, "CmnTex_Rock.bntx"),
            BntxReader.WriteLinear("CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb", 8, 8, 0x2006, new byte[64]));
        try
        {
            var library = new TextureLibrary(DumpFolder.Open(root));
            var image = library.Find(new ImportedModel(), "CmnTex_Rock_HrlField_UmeCliff_Near_A_Alb", Array.Empty<string>());
            Assert.NotNull(image);
            Assert.Contains("CmnTex_Rock.bntx", library.LastSource.Replace('\\', '/'));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void GraveyardPlantUsesTheProjectFile()
    {
        string root = Path.Combine(Path.GetTempPath(), "of-grave-" + Guid.NewGuid().ToString("N"));
        string romfs = Path.Combine(root, "romfs");
        Directory.CreateDirectory(Path.Combine(romfs, "Model"));
        Directory.CreateDirectory(Path.Combine(romfs, "Pack", "Actor"));
        File.WriteAllBytes(Path.Combine(romfs, "Model", "Land_Graveyard_Plants.Land_Graveyard_Plants.bfres"), File.ReadAllBytes(Fixture("v10-tree.bfres")));
        var pack = SarcArchive.Pack(new Dictionary<string, byte[]>
        {
            ["note.txt"] = System.Text.Encoding.ASCII.GetBytes("Model/Land_LonLon_Plant.Land_LonLon_Plant.bfres"),
        });
        File.WriteAllBytes(Path.Combine(romfs, "Pack", "Actor", "Land_LonLon_Plant.pack"), pack);
        File.WriteAllBytes(Path.Combine(romfs, "Model", "Land_LonLon_Plant.Land_LonLon_Plant.bfres"), File.ReadAllBytes(Fixture("v10-tree.bfres")));
        try
        {
            var grave = ModelLocator.Find(DumpFolder.Open(root), "Land_Graveyard_Plants_A_08", new List<string>());
            Assert.NotNull(grave);
            Assert.EndsWith("Model/Land_Graveyard_Plants.Land_Graveyard_Plants.bfres", grave!.Path.Replace('\\', '/'));
            var lon = ModelLocator.Find(DumpFolder.Open(root), "Land_LonLon_Plant_A_01", new List<string>());
            Assert.NotNull(lon);
            Assert.EndsWith("Model/Land_LonLon_Plant.Land_LonLon_Plant.bfres", lon!.Path.Replace('\\', '/'));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void LinearFskaSamplesTheTranslation()
    {
        var clips = FskaReader.Read(LinearClip(), 10);
        Assert.Single(clips);
        Assert.Equal("Walk", clips[0].Name);
        Assert.Equal(10, clips[0].FrameCount);
        Assert.False(clips[0].Euler);
        var bone = Assert.Single(clips[0].Bones);
        Assert.Equal("Root", bone.Bone);
        Assert.Equal(0, bone.Index);
        var start = bone.Keys.First(key => key.Frame == 0);
        var end = bone.Keys.First(key => key.Frame == 10);
        Assert.InRange(start.Px, -1e-4f, 1e-4f);
        Assert.InRange(end.Px, 10 - 1e-3f, 10 + 1e-3f);
        var mid = bone.Keys.FirstOrDefault(key => Math.Abs(key.Frame - 5) < 0.01f);
        if (mid is not null) Assert.InRange(mid.Px, 5 - 0.05f, 5 + 0.05f);
    }

    [Fact]
    public void CompactFskaUsesUnsignedKeysAndSeparateFrameValueAndSlopeScales()
    {
        var data = LinearClip();
        void U16(int at, ushort value) => System.Buffers.Binary.BinaryPrimitives.WriteUInt16LittleEndian(data.AsSpan(at), value);
        void F32(int at, float value) => System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(data.AsSpan(at), value);
        U16(0x290, 0x4C06);
        data[0x300] = 0;
        data[0x301] = 20;
        F32(0x2A0, 0.5f);
        F32(0x2A4, 0.00025f);
        F32(0x2A8, 10f);
        F32(0x320, 0.1f);
        F32(0x324, -1f);
        U16(0x328, 0);
        U16(0x32A, 10);
        U16(0x32C, 40000);
        U16(0x32E, 20);
        var keys = Assert.Single(Assert.Single(FskaReader.Read(data, 10)).Bones).Keys;
        Assert.Equal(new[] { 0f, 5f, 10f }, keys.Select(key => key.Frame));
        Assert.Equal(10f, keys[0].Px, 4);
        Assert.Equal(13.75f, keys[1].Px, 4);
        Assert.Equal(20f, keys[2].Px, 4);
    }

    [Theory]
    [InlineData("TestActor", false, true)]
    [InlineData("Animal_Epona_Adult", false, true)]
    [InlineData("Animal_Epona_Child", true, false)]
    public void ActorAnimationReferenceRespectsTheChildEponaRig(string gyaml, bool retarget, bool importsClip)
    {
        string root = Path.Combine(Path.GetTempPath(), "of-animation-" + Guid.NewGuid().ToString("N"));
        string romfs = Path.Combine(root, "romfs");
        Directory.CreateDirectory(Path.Combine(romfs, "Model"));
        Directory.CreateDirectory(Path.Combine(romfs, "Pack", "Actor"));
        try
        {
            const string component = "Component/AnimationParam/Shared.engine__component__AnimationParam.bgyml";
            var actor = new Dictionary<string, object?>
            {
                ["Components"] = new Dictionary<string, object?> { ["AnimationRef"] = "?" + component },
            };
            var animation = new Dictionary<string, object?>
            {
                ["AnimationResources"] = new List<object?>
                {
                    new Dictionary<string, object?> { ["ModelProjectName"] = "Shared", ["IsRetarget"] = retarget },
                },
            };
            File.WriteAllBytes(Path.Combine(romfs, "Pack", "Actor", gyaml + ".pack"),
                SarcArchive.Pack(new Dictionary<string, byte[]>
                {
                    [$"Actor/{gyaml}.engine__actor__ActorParam.bgyml"] = BymlWriter.Dump(actor),
                    [component] = BymlWriter.Dump(animation),
                }));
            var clip = LinearClip();
            "FRES    "u8.CopyTo(clip);
            clip[0x0A] = 10;
            clip[0x0C] = 0xFF;
            clip[0x0D] = 0xFE;
            File.WriteAllBytes(Path.Combine(romfs, "Model", "Shared.anim.bfres"), clip);
            var resolved = new ResolvedModel
            {
                Model = new ImportedModel
                {
                    Name = "DifferentModel",
                    Skeleton = new List<ImportedBone> { new() { Name = "Other" }, new() { Name = "Root" } },
                },
            };
            var notes = new List<string>();
            new AnimationLocator(DumpFolder.Open(root)).Attach(gyaml, resolved, notes);
            Assert.Contains($"Pack/Actor/{gyaml}.pack#{component}", resolved.Model.AnimationSources);
            if (importsClip)
            {
                Assert.Equal("Walk", Assert.Single(resolved.Model.Clips).Name);
                Assert.Contains("Model/Shared.anim.bfres", resolved.Model.AnimationSources);
            }
            else
            {
                Assert.Empty(resolved.Model.Clips);
                Assert.DoesNotContain("Model/Shared.anim.bfres", resolved.Model.AnimationSources);
            }
            Assert.Empty(notes);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void AstcSixBySixDecodesSrgbAlbedo()
    {
        // One LDR void-extent block with RGBA channels 255, 128, 0, 255.
        byte[] block = [0xFC, 0xFD, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x80, 0x80, 0, 0, 0xFF, 0xFF];
        var image = Assert.Single(BntxReader.Read(BntxReader.WriteLinear("Albedo", 6, 6, 0x3106, block)));
        Assert.True(image.Srgb);
        Assert.Equal(6, image.BlockW);
        Assert.Equal(6, image.BlockH);
        var rgba = AstcPixels.DecodeMipZero(image);
        Assert.Equal(6 * 6 * 4, rgba.Length);
        Assert.All(rgba.Chunk(4), pixel => Assert.Equal(new byte[] { 255, 128, 0, 255 }, pixel));
    }

    static byte[] LinearClip()
    {
        var data = new byte[0x400];
        void U64(int at, ulong value) => System.Buffers.Binary.BinaryPrimitives.WriteUInt64LittleEndian(data.AsSpan(at), value);
        void U32(int at, uint value) => System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(at), value);
        void U16(int at, ushort value) => System.Buffers.Binary.BinaryPrimitives.WriteUInt16LittleEndian(data.AsSpan(at), value);
        void F32(int at, float value) => System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(data.AsSpan(at), value);
        int StringAt(int at, string text)
        {
            var raw = System.Text.Encoding.UTF8.GetBytes(text);
            U16(at, (ushort)raw.Length);
            raw.CopyTo(data.AsSpan(at + 2));
            return at;
        }
        int walk = StringAt(0x360, "Walk");
        int root = StringAt(0x370, "Root");
        U64(0x58, 0x100);
        U64(0x60, 0x180);
        U32(0x184, 1);
        U64(0x1A0, (ulong)walk);
        data[0x100] = (byte)'F'; data[0x101] = (byte)'S'; data[0x102] = (byte)'K'; data[0x103] = (byte)'A';
        U64(0x108, (ulong)walk);
        U64(0x120, 0x340);
        U64(0x128, 0x200);
        U32(0x140, 10);
        U32(0x144, 1);
        U16(0x14C, 1);
        U64(0x200, (ulong)root);
        U64(0x208, 0x280);
        data[0x200 + 46] = 1;
        U16(0x340, 0);
        U64(0x280, 0x300);
        U64(0x288, 0x320);
        U16(0x290, 1 << 4);
        U16(0x292, 2);
        U32(0x294, 0x10);
        F32(0x2A0, 1);
        F32(0x2A8, 10);
        F32(0x300, 0);
        F32(0x304, 10);
        F32(0x320, 0);
        F32(0x324, 10);
        F32(0x328, 10);
        F32(0x32C, 0);
        return data;
    }

    [Fact]
    public void Bc4AndBc5StayOneAndTwoChannels()
    {
        var bc4 = BntxReader.Read(BntxReader.WriteLinear("Gn", 8, 8, 0x1D01, new byte[8 * 8 / 2]));
        Assert.Equal(GpuCodec.Bc4, bc4[0].Codec);
        Assert.Equal(8, bc4[0].BytesPerBlock);
        var bc5 = BntxReader.Read(BntxReader.WriteLinear("Nrm", 8, 8, 0x1E01, new byte[8 * 8]));
        Assert.Equal(GpuCodec.Bc5, bc5[0].Codec);
        Assert.Equal(16, bc5[0].BytesPerBlock);
    }

    static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true, PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    sealed class ExpectedModel
    {
        public string Name { get; set; } = "";
        public List<ExpectedPart> Parts { get; set; } = new();
    }

    sealed class ExpectedPart
    {
        public string Name { get; set; } = "";
        public string Material { get; set; } = "";
        public string Texture { get; set; } = "";
        public string AlbedoSampler { get; set; } = "";
        public string Alpha { get; set; } = "";
        [System.Text.Json.Serialization.JsonPropertyName("double_sided")]
        public bool DoubleSided { get; set; }
        public List<int> Indices { get; set; } = new();
        public List<double[]> Positions { get; set; } = new();
        public List<string[]> Binds { get; set; } = new();
    }
}
