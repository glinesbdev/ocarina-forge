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
        Assert.Contains("Land_LonLon_Plant_A", ModelLocator.PlacementStems("Land_LonLon_Plant_A_01"));
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
