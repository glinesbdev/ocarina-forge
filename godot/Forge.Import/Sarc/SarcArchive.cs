using System.Text;

namespace OcarinaForge.Import;

/// <summary>Little-endian SARC. Names are sorted by <c>h = h * 0x65 + byte</c>.</summary>
public sealed class SarcArchive
{
    readonly byte[] _data;
    readonly Dictionary<string, (int Start, int End)> _files;

    public SarcArchive(byte[] data)
    {
        if (data.Length < 0x14 || data[0] != (byte)'S' || data[1] != (byte)'A' || data[2] != (byte)'R' || data[3] != (byte)'C')
            throw new ImportException("not a SARC archive");
        if (data[6] != 0xFF || data[7] != 0xFE)
            throw new ImportException("only little-endian SARC is supported");
        _data = data;
        int dataAt = (int)Bin.U32(data, 0x0C);
        int header = Bin.U16(data, 4);
        if (header + 4 > data.Length || data[header] != (byte)'S' || data[header + 1] != (byte)'F')
            throw new ImportException("SARC is missing its file table");
        int count = Bin.U16(data, header + 6);
        int nodesAt = header + 0x0C;
        int namesAt = nodesAt + 16 * count + 8;
        _files = new Dictionary<string, (int, int)>(count);
        for (int index = 0; index < count; index++)
        {
            int at = nodesAt + 16 * index;
            uint attrs = Bin.U32(data, at + 4);
            int start = (int)Bin.U32(data, at + 8);
            int end = (int)Bin.U32(data, at + 12);
            string name;
            if ((attrs & 0x01000000) != 0)
            {
                int nameAt = namesAt + (int)(attrs & 0xFFFF) * 4;
                name = Bin.CString(data, nameAt, 512);
            }
            else name = $"(unnamed {index})";
            _files[name] = (dataAt + start, dataAt + end);
        }
    }

    public IReadOnlyCollection<string> Names => _files.Keys;

    public byte[] Read(string name)
    {
        var (start, end) = _files[name];
        var blob = new byte[end - start];
        Buffer.BlockCopy(_data, start, blob, 0, blob.Length);
        return blob;
    }

    public bool TryRead(string name, out byte[] blob)
    {
        if (!_files.TryGetValue(name, out var span))
        {
            blob = Array.Empty<byte>();
            return false;
        }
        blob = new byte[span.End - span.Start];
        Buffer.BlockCopy(_data, span.Start, blob, 0, blob.Length);
        return true;
    }

    public static uint NameHash(string name)
    {
        uint value = 0;
        foreach (byte item in Encoding.UTF8.GetBytes(name))
            value = value * 0x65 + item;
        return value;
    }

    public static byte[] Pack(IReadOnlyDictionary<string, byte[]> files)
    {
        var order = files.Keys.OrderBy(NameHash).ToList();
        var names = new List<byte>();
        var nameAt = new Dictionary<string, int>();
        foreach (string name in order)
        {
            nameAt[name] = names.Count / 4;
            names.AddRange(Encoding.UTF8.GetBytes(name));
            names.Add(0);
            while (names.Count % 4 != 0) names.Add(0);
        }
        int count = order.Count;
        const int sfatAt = 0x14;
        int namesAt = sfatAt + 0x0C + 16 * count + 8;
        int dataAt = Align(namesAt + names.Count, 4);
        var output = new List<byte>(dataAt + files.Values.Sum(item => item.Length + 16));
        output.AddRange(new byte[dataAt]);
        void Write(int at, byte[] word)
        {
            for (int i = 0; i < word.Length; i++) output[at + i] = word[i];
        }
        output[0] = (byte)'S'; output[1] = (byte)'A'; output[2] = (byte)'R'; output[3] = (byte)'C';
        Write(4, U16(0x14));
        output[6] = 0xFF; output[7] = 0xFE;
        Write(0x0C, U32((uint)dataAt));
        Write(0x10, U16(0x0100));
        output[sfatAt] = (byte)'S'; output[sfatAt + 1] = (byte)'F'; output[sfatAt + 2] = (byte)'A'; output[sfatAt + 3] = (byte)'T';
        Write(sfatAt + 4, U16(0x0C));
        Write(sfatAt + 6, U16((ushort)count));
        Write(sfatAt + 8, U32(0x65));
        int cursor = dataAt;
        for (int index = 0; index < count; index++)
        {
            string name = order[index];
            var blob = files[name];
            int node = sfatAt + 0x0C + 16 * index;
            Write(node, U32(NameHash(name)));
            Write(node + 4, U32(0x01000000u | (uint)nameAt[name]));
            int start = cursor - dataAt;
            cursor += blob.Length;
            int end = cursor - dataAt;
            while (cursor % 4 != 0) cursor++;
            Write(node + 8, U32((uint)start));
            Write(node + 12, U32((uint)end));
        }
        int sfnt = sfatAt + 0x0C + 16 * count;
        output[sfnt] = (byte)'S'; output[sfnt + 1] = (byte)'F'; output[sfnt + 2] = (byte)'N'; output[sfnt + 3] = (byte)'T';
        Write(sfnt + 4, U16(0x08));
        Write(sfnt + 6, U16(0));
        for (int i = 0; i < names.Count; i++) output[namesAt + i] = names[i];
        cursor = dataAt;
        foreach (string name in order)
        {
            var blob = files[name];
            output.AddRange(blob);
            cursor += blob.Length;
            while (cursor % 4 != 0)
            {
                output.Add(0);
                cursor++;
            }
        }
        Write(8, U32((uint)output.Count));
        return output.ToArray();
    }

    static int Align(int value, int boundary) => (value + boundary - 1) & ~(boundary - 1);
    static byte[] U16(ushort value) => new[] { (byte)value, (byte)(value >> 8) };
    static byte[] U32(uint value) => new[] { (byte)value, (byte)(value >> 8), (byte)(value >> 16), (byte)(value >> 24) };
}
