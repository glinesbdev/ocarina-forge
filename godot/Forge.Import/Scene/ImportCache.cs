using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace OcarinaForge.Import;

/// <summary>
/// Neutral cache (not a Godot <c>.res</c>) so the second load skips BFRES and BNTX.
/// Magic <c>OFM1</c>. Lives outside the repo.
/// </summary>
public static class ImportCache
{
    public const string Magic = "OFM1";
    public const int Version = 1;
    public const string ImporterVersion = "godot-spike-1";

    public static string DefaultDirectory()
    {
        string? env = Environment.GetEnvironmentVariable("OCARINA_FORGE_GODOT_CACHE");
        if (!string.IsNullOrWhiteSpace(env)) return env;
        if (OperatingSystem.IsWindows())
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(local, "OcarinaForge", "godot-cache");
        }
        string home = Environment.GetEnvironmentVariable("XDG_CACHE_HOME") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cache");
        return Path.Combine(home, "ocarina-forge", "godot");
    }

    public static string KeyFor(DumpFolder dump, string banc)
    {
        using var sha = SHA256.Create();
        var builder = new StringBuilder(ImporterVersion);
        builder.Append('\n').Append(banc);
        foreach (string relative in new[]
        {
            banc,
            banc.Replace("_Static", "_Dynamic", StringComparison.Ordinal),
        })
        {
            if (!dump.Exists(relative)) continue;
            builder.Append('\n').Append(relative).Append(' ').Append(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Path.Combine(dump.Romfs, relative.Replace('/', Path.DirectorySeparatorChar))))));
        }
        return Convert.ToHexString(sha.ComputeHash(Encoding.UTF8.GetBytes(builder.ToString())))[..32];
    }

    public static string PathFor(string directory, string key) => Path.Combine(directory, key + ".ofm");

    public static void Save(string path, ImportedField field)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var blobs = new List<byte[]>();
        var doc = Document(field, blobs);
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(doc);
        using var stream = File.Create(path);
        using var writer = new BinaryWriter(stream);
        writer.Write(Encoding.ASCII.GetBytes(Magic));
        writer.Write(Version);
        writer.Write(json.Length);
        writer.Write(json);
        writer.Write(blobs.Count);
        foreach (var blob in blobs)
        {
            writer.Write(blob.Length);
            writer.Write(blob);
        }
    }

    public static ImportedField? TryLoad(string path)
    {
        if (!File.Exists(path)) return null;
        using var stream = File.OpenRead(path);
        using var reader = new BinaryReader(stream);
        var magic = Encoding.ASCII.GetString(reader.ReadBytes(4));
        if (magic != Magic || reader.ReadInt32() != Version) return null;
        int jsonLength = reader.ReadInt32();
        var doc = JsonSerializer.Deserialize<CacheDoc>(reader.ReadBytes(jsonLength));
        if (doc is null || doc.Importer != ImporterVersion) return null;
        int count = reader.ReadInt32();
        var blobs = new List<byte[]>(count);
        for (int i = 0; i < count; i++)
        {
            int length = reader.ReadInt32();
            blobs.Add(reader.ReadBytes(length));
        }
        return FromDocument(doc, blobs);
    }

    static CacheDoc Document(ImportedField field, List<byte[]> blobs)
    {
        var doc = new CacheDoc
        {
            Importer = ImporterVersion,
            Banc = field.Banc,
            Scene = field.Scene,
            Notes = field.Notes,
            GeometryBytes = field.GeometryBytes,
            Bc7Bytes = field.Bc7Bytes,
        };
        foreach (var actor in field.Actors)
        {
            var stored = new CacheActor
            {
                Gyaml = actor.Placement.Gyaml,
                Hash = actor.Placement.Hash,
                Name = actor.Placement.Name,
                Source = actor.Placement.Source,
                X = actor.Placement.X, Y = actor.Placement.Y, Z = actor.Placement.Z,
                Rx = actor.Placement.Rx, Ry = actor.Placement.Ry, Rz = actor.Placement.Rz,
                Sx = actor.Placement.Sx, Sy = actor.Placement.Sy, Sz = actor.Placement.Sz,
                Draw = actor.Placement.Draw,
                Skip = actor.Placement.SkipReason,
                ModelPath = actor.ModelPath,
                Note = actor.Note,
            };
            if (actor.Model is not null)
            {
                stored.Model = actor.Model.Name;
                foreach (var part in actor.Model.Parts)
                    stored.Parts.Add(PackPart(part, blobs));
            }
            foreach (var (name, image) in actor.Textures)
                stored.Textures.Add(PackTexture(name, image, blobs));
            doc.Actors.Add(stored);
        }
        if (field.Terrain is not null) doc.Terrain = PackPart(field.Terrain, blobs);
        return doc;
    }

    static ImportedField FromDocument(CacheDoc doc, List<byte[]> blobs)
    {
        var field = new ImportedField
        {
            Banc = doc.Banc,
            Scene = doc.Scene,
            Notes = doc.Notes,
            GeometryBytes = doc.GeometryBytes,
            Bc7Bytes = doc.Bc7Bytes,
            Terrain = doc.Terrain is null ? null : UnpackPart(doc.Terrain, blobs),
        };
        var grouped = new Dictionary<string, ImportedModel>(StringComparer.Ordinal);
        foreach (var actor in doc.Actors)
        {
            ImportedModel? model = null;
            if (actor.Model.Length > 0)
            {
                if (!grouped.TryGetValue(actor.ModelPath + "|" + actor.Model, out model))
                {
                    model = new ImportedModel { Name = actor.Model, Parts = actor.Parts.Select(item => UnpackPart(item, blobs)).ToList() };
                    grouped[actor.ModelPath + "|" + actor.Model] = model;
                }
            }
            var textures = new Dictionary<string, CompressedTexture>(StringComparer.OrdinalIgnoreCase);
            foreach (var texture in actor.Textures)
                textures[texture.Name] = UnpackTexture(texture, blobs);
            field.Actors.Add(new FieldActor
            {
                Placement = new ActorPlacement
                {
                    Gyaml = actor.Gyaml, Hash = actor.Hash, Name = actor.Name, Source = actor.Source,
                    X = actor.X, Y = actor.Y, Z = actor.Z, Rx = actor.Rx, Ry = actor.Ry, Rz = actor.Rz,
                    Sx = actor.Sx, Sy = actor.Sy, Sz = actor.Sz, Draw = actor.Draw, SkipReason = actor.Skip,
                },
                ModelPath = actor.ModelPath,
                Model = model,
                Textures = textures,
                Note = actor.Note,
            });
        }
        field.Drawables = field.Actors.Count(item => item.Model is not null);
        return field;
    }

    static CachePart PackPart(ImportedPart part, List<byte[]> blobs)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        writer.Write(part.Positions.Count);
        foreach (var p in part.Positions) { writer.Write((float)p.X); writer.Write((float)p.Y); writer.Write((float)p.Z); }
        writer.Write(part.Indices.Count);
        foreach (int index in part.Indices) writer.Write(index);
        writer.Write(part.Uvs?.Count ?? 0);
        if (part.Uvs is not null)
            foreach (var uv in part.Uvs) { writer.Write((float)uv.U); writer.Write((float)uv.V); }
        int id = blobs.Count;
        blobs.Add(stream.ToArray());
        return new CachePart
        {
            Name = part.Name, Lod = part.Lod, Material = part.Material, Texture = part.Texture,
            Sampler = part.AlbedoSampler, Alpha = part.Alpha, DoubleSided = part.DoubleSided,
            Omit = part.OmitReason, Water = part.Water, Blob = id,
        };
    }

    static ImportedPart UnpackPart(CachePart part, List<byte[]> blobs)
    {
        using var reader = new BinaryReader(new MemoryStream(blobs[part.Blob]));
        int vertices = reader.ReadInt32();
        var positions = new List<Vec3>(vertices);
        for (int i = 0; i < vertices; i++)
            positions.Add(new Vec3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle()));
        int indexCount = reader.ReadInt32();
        var indices = new List<int>(indexCount);
        for (int i = 0; i < indexCount; i++) indices.Add(reader.ReadInt32());
        int uvCount = reader.ReadInt32();
        List<Vec2>? uvs = null;
        if (uvCount > 0)
        {
            uvs = new List<Vec2>(uvCount);
            for (int i = 0; i < uvCount; i++) uvs.Add(new Vec2(reader.ReadSingle(), reader.ReadSingle()));
        }
        return new ImportedPart
        {
            Name = part.Name, Lod = part.Lod, Positions = positions, Indices = indices, Uvs = uvs,
            Material = part.Material, Texture = part.Texture, AlbedoSampler = part.Sampler,
            Alpha = part.Alpha, DoubleSided = part.DoubleSided, OmitReason = part.Omit, Water = part.Water,
        };
    }

    static CacheTexture PackTexture(string name, CompressedTexture image, List<byte[]> blobs)
    {
        int id = blobs.Count;
        blobs.Add(image.Blocks);
        return new CacheTexture
        {
            Name = name, Width = image.Width, Height = image.Height, Format = image.FormatId,
            Codec = image.Codec.ToString(), Srgb = image.Srgb, Blob = id, Block = image.BytesPerBlock,
        };
    }

    static CompressedTexture UnpackTexture(CacheTexture texture, List<byte[]> blobs) => new()
    {
        Name = texture.Name, Width = texture.Width, Height = texture.Height, FormatId = texture.Format,
        Codec = Enum.TryParse<GpuCodec>(texture.Codec, out var codec) ? codec : GpuCodec.Unknown,
        Srgb = texture.Srgb, Blocks = blobs[texture.Blob], BytesPerBlock = texture.Block,
    };

    sealed class CacheDoc
    {
        public string Importer { get; set; } = "";
        public string Banc { get; set; } = "";
        public string Scene { get; set; } = "";
        public List<string> Notes { get; set; } = new();
        public long GeometryBytes { get; set; }
        public long Bc7Bytes { get; set; }
        public List<CacheActor> Actors { get; set; } = new();
        public CachePart? Terrain { get; set; }
    }

    sealed class CacheActor
    {
        public string Gyaml { get; set; } = "";
        public ulong Hash { get; set; }
        public string Name { get; set; } = "";
        public string Source { get; set; } = "";
        public double X { get; set; }
        public double Y { get; set; }
        public double Z { get; set; }
        public double Rx { get; set; }
        public double Ry { get; set; }
        public double Rz { get; set; }
        public double Sx { get; set; }
        public double Sy { get; set; }
        public double Sz { get; set; }
        public bool Draw { get; set; }
        public string Skip { get; set; } = "";
        public string ModelPath { get; set; } = "";
        public string Model { get; set; } = "";
        public string Note { get; set; } = "";
        public List<CachePart> Parts { get; set; } = new();
        public List<CacheTexture> Textures { get; set; } = new();
    }

    sealed class CachePart
    {
        public string Name { get; set; } = "";
        public int Lod { get; set; }
        public string Material { get; set; } = "";
        public string Texture { get; set; } = "";
        public string Sampler { get; set; } = "";
        public string Alpha { get; set; } = "";
        public bool DoubleSided { get; set; }
        public string Omit { get; set; } = "";
        public bool Water { get; set; }
        public int Blob { get; set; }
    }

    sealed class CacheTexture
    {
        public string Name { get; set; } = "";
        public int Width { get; set; }
        public int Height { get; set; }
        public int Format { get; set; }
        public string Codec { get; set; } = "";
        public bool Srgb { get; set; }
        public int Block { get; set; }
        public int Blob { get; set; }
    }
}
