using ZstdSharp;

namespace OcarinaForge.Import;

/// <summary>
/// Zstd frames that may need a dictionary from <c>Pack/ZsDic.pack.zs</c>.
/// Dictionary ids on 1.1.1: 1 everything else, 2 <c>.bkprb.zs</c>, 3 placement, 4 actor packs.
/// The dictionary pack itself is plain zstd.
/// </summary>
public sealed class ZstdDictionaries
{
    readonly Dictionary<uint, byte[]> _byId;
    public string Source { get; }

    ZstdDictionaries(Dictionary<uint, byte[]> byId, string source)
    {
        _byId = byId;
        Source = source;
    }

    public static ZstdDictionaries Load(string path)
    {
        if (!File.Exists(path))
            throw new ImportException($"No dictionary pack at {path}. Point the viewer at the dump root that contains romfs/Pack/ZsDic.pack.zs.");
        var raw = File.ReadAllBytes(path);
        try
        {
            if (ZstdFrames.IsZstd(raw)) raw = ZstdFrames.Decompress(raw, null);
            var archive = new SarcArchive(raw);
            var byId = new Dictionary<uint, byte[]>();
            foreach (string name in archive.Names)
            {
                if (!name.EndsWith(".zsdic", StringComparison.OrdinalIgnoreCase)) continue;
                var blob = archive.Read(name);
                uint id = ZstdFrames.DictionaryId(blob);
                if (id != 0) byId[id] = blob;
            }
            if (byId.Count == 0)
                throw new ImportException($"{path} unpacked, but it has no .zsdic files.");
            return new ZstdDictionaries(byId, path);
        }
        catch (ImportException)
        {
            throw;
        }
        catch (Exception exc)
        {
            throw new ImportException($"Could not read the dictionary pack {path}: {exc.Message}", exc);
        }
    }

    public bool Contains(uint id) => _byId.ContainsKey(id);

    public byte[] Require(uint id)
    {
        if (_byId.TryGetValue(id, out var blob)) return blob;
        throw new ImportException($"dictionary id {id} is not in {Source}");
    }
}

public static class ZstdFrames
{
    public static bool IsZstd(ReadOnlySpan<byte> data) =>
        data.Length >= 4 && data[0] == 0x28 && data[1] == 0xB5 && data[2] == 0x2F && data[3] == 0xFD;

    public static uint FrameDictionaryId(ReadOnlySpan<byte> data)
    {
        if (!IsZstd(data) || data.Length < 6) return 0;
        byte descriptor = data[4];
        int dictFlag = descriptor & 3;
        if (dictFlag == 0) return 0;
        bool single = (descriptor & 0x20) != 0;
        int pos = 5;
        if (!single)
        {
            if (pos >= data.Length) return 0;
            pos += 1;
        }
        int width = dictFlag == 1 ? 1 : dictFlag == 2 ? 2 : 4;
        if (pos + width > data.Length) return 0;
        uint id = 0;
        for (int i = 0; i < width; i++) id |= (uint)data[pos + i] << (8 * i);
        return id;
    }

    /// <summary>Zstd dictionary magic <c>0xEC30A437</c>, then the u32 id.</summary>
    public static uint DictionaryId(ReadOnlySpan<byte> dictionary)
    {
        if (dictionary.Length < 8) return 0;
        if (Bin.U32(dictionary, 0) != 0xEC30A437) return 0;
        return Bin.U32(dictionary, 4);
    }

    public static byte[] Decompress(byte[] data, ZstdDictionaries? dictionaries)
    {
        if (!IsZstd(data)) return data;
        uint dictId = FrameDictionaryId(data);
        using var decoder = new Decompressor();
        if (dictId != 0)
        {
            if (dictionaries is null || !dictionaries.Contains(dictId))
            {
                string where = dictionaries?.Source ?? "no dictionary pack was loaded";
                throw new ImportException(
                    $"This file is zstd with dictionary id {dictId}, which is not in {where}. " +
                    "Placement files need id 3 from romfs/Pack/ZsDic.pack.zs.");
            }
            decoder.LoadDictionary(dictionaries.Require(dictId));
        }
        try
        {
            return decoder.Unwrap(data).ToArray();
        }
        catch (Exception exc)
        {
            throw new ImportException($"zstd could not decompress this file (dictionary {dictId}): {exc.Message}", exc);
        }
    }

    public static byte[] Compress(byte[] data, byte[]? dictionary = null)
    {
        using var compressor = new Compressor(3);
        if (dictionary is not null) compressor.LoadDictionary(dictionary);
        return compressor.Wrap(data).ToArray();
    }
}
