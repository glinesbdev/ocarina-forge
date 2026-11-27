namespace OcarinaForge.Import;

/// <summary>A tiny dump with no Nintendo assets, used by the headless self-test and the unit tests.</summary>
public static class SyntheticDump
{
    public static string Write(string root)
    {
        string romfs = Path.Combine(root, "romfs");
        Directory.CreateDirectory(Path.Combine(romfs, "Banc", "HyruleField"));
        Directory.CreateDirectory(Path.Combine(romfs, "Banc", "GameBancParam"));
        Directory.CreateDirectory(Path.Combine(romfs, "Model"));
        Directory.CreateDirectory(Path.Combine(romfs, "Tex"));
        Directory.CreateDirectory(Path.Combine(romfs, "TerrainArc", "Field"));
        File.WriteAllBytes(Path.Combine(romfs, "Model", "Obj_Tree.Obj_Tree.bfres"), TreeBytes());
        File.WriteAllBytes(Path.Combine(romfs, "Tex", "Bark_Alb.bntx"), Bark());
        File.WriteAllBytes(Path.Combine(romfs, "Banc", "HyruleField", "HyruleField_Child_Static.bcett.byml"), Banc(new (double, double, double, double)[]
        {
            (1.5, 10, -3, 0),
            (8, 10, 4, Math.PI / 2),
        }));
        File.WriteAllBytes(Path.Combine(romfs, "Banc", "HyruleField", "HyruleField_Child_Dynamic.bcett.byml"), Banc(new[]
        {
            (20.0, 10.0, 0.0, 0.0),
        }));
        File.WriteAllBytes(
            Path.Combine(romfs, "Banc", "GameBancParam", "HyruleField_Child.game__banc__GameBancParam.bgyml"),
            BymlWriter.Dump(new Dictionary<string, object?>
            {
                ["TerrainSceneName"] = "Field",
                ["TerrainScenePosition"] = new Dictionary<string, object?> { ["X"] = 0f, ["Y"] = 0f, ["Z"] = 0f },
            }));
        File.WriteAllBytes(Path.Combine(romfs, "TerrainArc", "Field.tscb"), Tscb());
        File.WriteAllBytes(Path.Combine(romfs, "TerrainArc", "Field", "ground.hght"), Height());
        return root;
    }

    public static byte[] TreeBytes()
    {
        using var stream = typeof(SyntheticDump).Assembly.GetManifestResourceStream("v10-tree.bfres");
        if (stream is null) throw new ImportException("embedded v10-tree.bfres is missing");
        using var memory = new MemoryStream();
        stream.CopyTo(memory);
        return memory.ToArray();
    }

    public static byte[] Bark()
    {
        var blocks = new byte[64];
        for (int i = 0; i < blocks.Length; i++) blocks[i] = (byte)(0xA0 + (i & 0x0F));
        return BntxReader.WriteLinear("Bark_Alb", 8, 8, 0x2006, blocks);
    }

    static byte[] Banc((double X, double Y, double Z, double Yaw)[] actors)
    {
        var list = new List<object?>();
        for (int i = 0; i < actors.Length; i++)
        {
            var actor = actors[i];
            list.Add(new Dictionary<string, object?>
            {
                ["Gyaml"] = "Obj_Tree",
                ["Hash"] = new BymlU64((ulong)(0x11 + i)),
                ["Translate"] = new List<object?> { (float)actor.X, (float)actor.Y, (float)actor.Z },
                ["Rotate"] = new List<object?> { 0f, (float)actor.Yaw, 0f },
                ["Scale"] = new List<object?> { 1f, 2f, 1f },
                ["Name"] = "oak",
            });
        }
        return BymlWriter.Dump(new Dictionary<string, object?> { ["Actors"] = list });
    }

    static byte[] Tscb()
    {
        var blob = new byte[0x120];
        blob[0] = (byte)'T'; blob[1] = (byte)'S'; blob[2] = (byte)'C'; blob[3] = (byte)'B';
        WriteF(blob, 0x10, 1f);
        WriteF(blob, 0x14, 100f);
        WriteU(blob, 0x38, 1);
        int tile = 0x90;
        WriteU(blob, 0x78, (uint)(tile - 0x78));
        WriteF(blob, tile, 0f);
        WriteF(blob, tile + 4, 0f);
        WriteF(blob, tile + 8, 256f);
        int name = tile + 0x70;
        var raw = "ground"u8;
        raw.CopyTo(blob.AsSpan(name));
        WriteU(blob, tile + 0x60, (uint)(name - (tile + 0x60)));
        return blob;
    }

    static byte[] Height()
    {
        int count = TerrainMesh.Size * TerrainMesh.Size;
        var samples = new byte[count * 4];
        var word = BitConverter.GetBytes(0.5f);
        for (int i = 0; i < count; i++) word.CopyTo(samples, i * 4);
        var payload = new byte[0x40 + samples.Length];
        WriteU(payload, 0x2C, 0x40 - 0x2C);
        WriteU(payload, 0x30, (uint)samples.Length);
        samples.CopyTo(payload, 0x40);
        return payload;
    }

    static void WriteU(byte[] data, int at, uint value) => System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(data.AsSpan(at), value);
    static void WriteF(byte[] data, int at, float value) => System.Buffers.Binary.BinaryPrimitives.WriteSingleLittleEndian(data.AsSpan(at), value);
}
