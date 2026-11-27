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
