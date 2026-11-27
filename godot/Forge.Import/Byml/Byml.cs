using System.Buffers.Binary;
using System.Text;

namespace OcarinaForge.Import;

/// <summary>BYML version 7, little endian, magic <c>YB</c>. Hash maps are kept.</summary>
public static class Byml
{
    public static object? Load(ReadOnlySpan<byte> data)
    {
        if (data.Length < 16 || data[0] != (byte)'Y' || data[1] != (byte)'B')
            throw new ImportException("not a little-endian BYML (expected magic YB)");
        var bytes = data.ToArray();
        var reader = new Reader(bytes);
        int keysAt = (int)Bin.U32(bytes, 4);
        int stringsAt = (int)Bin.U32(bytes, 8);
        int rootAt = (int)Bin.U32(bytes, 12);
        reader.Keys = keysAt != 0 ? reader.Table(keysAt) : new List<string>();
        reader.Strings = stringsAt != 0 ? reader.Table(stringsAt) : new List<string>();
        if (rootAt == 0) return null;
        return reader.Node(bytes[rootAt], rootAt);
    }

    sealed class Reader
    {
        readonly byte[] _data;
        public List<string> Keys = new();
        public List<string> Strings = new();

        public Reader(byte[] data) => _data = data;

        public List<string> Table(int at)
        {
            int count = _data[at + 1] | (_data[at + 2] << 8) | (_data[at + 3] << 16);
            var offsets = new int[count + 1];
            for (int i = 0; i <= count; i++) offsets[i] = (int)Bin.U32(_data, at + 4 + 4 * i);
            var result = new List<string>(count);
            for (int i = 0; i < count; i++)
            {
                int start = at + offsets[i];
                int end = start;
                while (end < _data.Length && _data[end] != 0) end++;
                result.Add(Encoding.UTF8.GetString(_data, start, end - start));
            }
            return result;
        }

        public object? Value(int kind, uint raw)
        {
            if (kind is 0xC0 or 0xC1 or 0x20 or 0x21) return Node(kind, (int)raw);
            if (kind == 0xA0) return Strings[(int)raw];
            if (kind == 0xA1)
            {
                int size = (int)Bin.U32(_data, (int)raw);
                var blob = new byte[size];
                Buffer.BlockCopy(_data, (int)raw + 4, blob, 0, size);
                return blob;
            }
            if (kind == 0xD0) return raw != 0;
            if (kind == 0xD1) return unchecked((int)raw);
            if (kind == 0xD2) return BitConverter.UInt32BitsToSingle(raw);
            if (kind == 0xD3) return new BymlU32(raw);
            if (kind == 0xD4) return new BymlS64(Bin.I64(_data, (int)raw));
            if (kind == 0xD5) return new BymlU64(Bin.U64(_data, (int)raw));
            if (kind == 0xD6) return new BymlF64(BitConverter.Int64BitsToDouble(Bin.I64(_data, (int)raw)));
            if (kind == 0xFF) return null;
            throw new ImportException($"unknown BYML type 0x{kind:X}");
        }

        public object Node(int kind, int at)
        {
            int count = _data[at + 1] | (_data[at + 2] << 8) | (_data[at + 3] << 16);
            if (kind == 0xC0)
            {
                int vals = at + 4 + ((count + 3) & ~3);
                var list = new List<object?>(count);
                for (int i = 0; i < count; i++)
                    list.Add(Value(_data[at + 4 + i], Bin.U32(_data, vals + 4 * i)));
                return list;
            }
            if (kind == 0xC1)
            {
                var dict = new Dictionary<string, object?>(count);
                for (int i = 0; i < count; i++)
                {
                    int pos = at + 4 + 8 * i;
                    int key = _data[pos] | (_data[pos + 1] << 8) | (_data[pos + 2] << 16);
                    dict[Keys[key]] = Value(_data[pos + 3], Bin.U32(_data, pos + 4));
                }
                return dict;
            }
            if (kind is 0x20 or 0x21)
            {
                bool wide = kind == 0x21;
                int stride = wide ? 12 : 8;
                int typesAt = at + 4 + stride * count;
                if (wide)
                {
                    var map = new BymlHash64();
                    for (int i = 0; i < count; i++)
                    {
                        int pos = at + 4 + stride * i;
                        map[Bin.U64(_data, pos)] = Value(_data[typesAt + i], Bin.U32(_data, pos + stride - 4));
                    }
                    return map;
                }
                var narrow = new BymlHash32();
                for (int i = 0; i < count; i++)
                {
                    int pos = at + 4 + stride * i;
                    narrow[Bin.U32(_data, pos)] = Value(_data[typesAt + i], Bin.U32(_data, pos + stride - 4));
                }
                return narrow;
            }
            throw new ImportException($"unknown BYML container 0x{kind:X}");
        }
    }
}

public readonly record struct BymlU32(uint Value);
public readonly record struct BymlS64(long Value);
public readonly record struct BymlU64(ulong Value);
public readonly record struct BymlF64(double Value);

public sealed class BymlHash32 : Dictionary<uint, object?> { }
public sealed class BymlHash64 : Dictionary<ulong, object?> { }

public static class BymlWriter
{
    public static byte[] Dump(object? root)
    {
        var keys = new HashSet<string>();
        var strings = new HashSet<string>();
        Collect(root, keys, strings);
        var output = new List<byte>(256);
        output.AddRange(new byte[16]);
        int keysAt = 0;
        Dictionary<string, int> keyIndex = new();
        if (keys.Count > 0)
        {
            keysAt = output.Count;
            var (table, index) = StringTable(keys);
            keyIndex = index;
            output.AddRange(table);
        }
        int stringsAt = 0;
        Dictionary<string, int> stringIndex = new();
        if (strings.Count > 0)
        {
            stringsAt = output.Count;
            var (table, index) = StringTable(strings);
            stringIndex = index;
            output.AddRange(table);
        }
        var seen = new Dictionary<string, int>();
        int rootAt = root is null ? 0 : Container(root, output, keyIndex, stringIndex, seen);
        while (output.Count % 4 != 0) output.Add(0);
        var bytes = output.ToArray();
        bytes[0] = (byte)'Y';
        bytes[1] = (byte)'B';
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(2), 7);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(4), keysAt);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(8), stringsAt);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(12), rootAt);
        return bytes;
    }

    static void Collect(object? value, HashSet<string> keys, HashSet<string> strings)
    {
        switch (value)
        {
            case BymlHash32 map:
                foreach (var item in map.Values) Collect(item, keys, strings);
                break;
            case BymlHash64 map:
                foreach (var item in map.Values) Collect(item, keys, strings);
                break;
            case Dictionary<string, object?> dict:
                foreach (var (key, item) in dict)
                {
                    keys.Add(key);
                    Collect(item, keys, strings);
                }
                break;
            case IList<object?> list:
                foreach (var item in list) Collect(item, keys, strings);
                break;
            case string text:
                strings.Add(text);
                break;
        }
    }

    static (byte[] Bytes, Dictionary<string, int> Index) StringTable(IEnumerable<string> names)
    {
        var ordered = names.OrderBy(text => Encoding.UTF8.GetBytes(text), ByteListComparer.Instance).ToList();
        int count = ordered.Count;
        int head = 4 + 4 * (count + 1);
        var blob = new List<byte>();
        var offsets = new int[count + 1];
        for (int i = 0; i < count; i++)
        {
            offsets[i] = head + blob.Count;
            blob.AddRange(Encoding.UTF8.GetBytes(ordered[i]));
            blob.Add(0);
        }
        offsets[count] = head + blob.Count;
        var output = new List<byte> { 0xC2, (byte)count, (byte)(count >> 8), (byte)(count >> 16) };
        foreach (int offset in offsets)
        {
            var word = new byte[4];
            BinaryPrimitives.WriteInt32LittleEndian(word, offset);
            output.AddRange(word);
        }
        output.AddRange(blob);
        while (output.Count % 4 != 0) output.Add(0);
        var index = new Dictionary<string, int>(count);
        for (int i = 0; i < count; i++) index[ordered[i]] = i;
        return (output.ToArray(), index);
    }

    static int Kind(object? value) => value switch
    {
        null => 0xFF,
        bool => 0xD0,
        string => 0xA0,
        byte[] => 0xA1,
        BymlU32 => 0xD3,
        BymlS64 => 0xD4,
        BymlU64 => 0xD5,
        BymlF64 => 0xD6,
        int => 0xD1,
        float => 0xD2,
        BymlHash64 => 0x21,
        BymlHash32 => 0x20,
        IList<object?> => 0xC0,
        Dictionary<string, object?> => 0xC1,
        _ => throw new ImportException($"can't store {value.GetType().Name} in BYML"),
    };

    static void Align(List<byte> output, int boundary)
    {
        while (output.Count % boundary != 0) output.Add(0);
    }

    static uint Scalar(object? value, Dictionary<string, int> stringIndex) => value switch
    {
        string text => (uint)stringIndex[text],
        bool flag => flag ? 1u : 0u,
        int number => unchecked((uint)number),
        float number => BitConverter.SingleToUInt32Bits(number),
        BymlU32 number => number.Value,
        null => 0u,
        _ => throw new ImportException($"BYML scalar {value}"),
    };

    static int Big(object value, List<byte> output, Dictionary<string, int> seen)
    {
        int kind = Kind(value);
        string signature = kind + ":" + value switch
        {
            byte[] blob => Convert.ToHexString(blob),
            BymlS64 number => number.Value.ToString(),
            BymlU64 number => number.Value.ToString(),
            BymlF64 number => number.Value.ToString("R"),
            _ => value.ToString() ?? "",
        };
        if (seen.TryGetValue(signature, out int existing)) return existing;
        Align(output, kind == 0xA1 ? 4 : 8);
        int at = output.Count;
        switch (value)
        {
            case BymlS64 number:
                var s64 = new byte[8];
                BinaryPrimitives.WriteInt64LittleEndian(s64, number.Value);
                output.AddRange(s64);
                break;
            case BymlU64 number:
                var u64 = new byte[8];
                BinaryPrimitives.WriteUInt64LittleEndian(u64, number.Value);
                output.AddRange(u64);
                break;
            case BymlF64 number:
                var f64 = new byte[8];
                BinaryPrimitives.WriteInt64LittleEndian(f64, BitConverter.DoubleToInt64Bits(number.Value));
                output.AddRange(f64);
                break;
            case byte[] blob:
                var size = new byte[4];
                BinaryPrimitives.WriteInt32LittleEndian(size, blob.Length);
                output.AddRange(size);
                output.AddRange(blob);
                break;
            default:
                throw new ImportException("not a relocated BYML value");
        }
        seen[signature] = at;
        return at;
    }

    static int Container(object value, List<byte> output, Dictionary<string, int> keyIndex, Dictionary<string, int> stringIndex, Dictionary<string, int> seen)
    {
        string signature = Signature(value);
        if (seen.TryGetValue(signature, out int existing)) return existing;
        Align(output, 4);
        int at = output.Count;
        seen[signature] = at;
        List<(int Index, object? Item)> items;
        int valsAt;
        int stride;
        if (value is IList<object?> list)
        {
            int count = list.Count;
            output.Add(0xC0);
            output.Add((byte)count);
            output.Add((byte)(count >> 8));
            output.Add((byte)(count >> 16));
            foreach (var item in list) output.Add((byte)Kind(item));
            while (output.Count % 4 != 0) output.Add(0);
            valsAt = output.Count;
            output.AddRange(new byte[4 * count]);
            items = list.Select((item, index) => (index, item)).ToList();
            stride = 4;
        }
        else if (value is Dictionary<string, object?> dict)
        {
            var ordered = dict.Keys.OrderBy(key => keyIndex[key]).ToList();
            int count = ordered.Count;
            output.Add(0xC1);
            output.Add((byte)count);
            output.Add((byte)(count >> 8));
            output.Add((byte)(count >> 16));
            foreach (string key in ordered)
            {
                int index = keyIndex[key];
                output.Add((byte)index);
                output.Add((byte)(index >> 8));
                output.Add((byte)(index >> 16));
                output.Add((byte)Kind(dict[key]));
                output.AddRange(new byte[4]);
            }
            valsAt = at + 8;
            stride = 8;
            items = ordered.Select((key, index) => (index, dict[key])).ToList();
        }
        else
        {
            throw new ImportException("BYML writer stores arrays and string dictionaries");
        }
        var bytes = output;
        foreach (var (index, item) in items)
        {
            int kind = Kind(item);
            int raw = kind is 0xC0 or 0xC1 or 0x20 or 0x21
                ? Container(item!, output, keyIndex, stringIndex, seen)
                : kind is 0xD4 or 0xD5 or 0xD6 or 0xA1
                    ? Big(item!, output, seen)
                    : (int)Scalar(item, stringIndex);
            int slot = valsAt + stride * index;
            while (bytes.Count < slot + 4) bytes.Add(0);
            var word = bytes.GetRange(slot, 4).ToArray();
            BinaryPrimitives.WriteInt32LittleEndian(word, raw);
            for (int i = 0; i < 4; i++) bytes[slot + i] = word[i];
        }
        return at;
    }

    static string Signature(object? value)
    {
        return value switch
        {
            null => "null",
            bool flag => "b" + flag,
            string text => "s" + text,
            int number => "i" + number,
            float number => "f" + BitConverter.SingleToInt32Bits(number),
            BymlU32 number => "u32" + number.Value,
            BymlU64 number => "u64" + number.Value,
            BymlS64 number => "s64" + number.Value,
            IList<object?> list => "a[" + string.Join(",", list.Select(Signature)) + "]",
            Dictionary<string, object?> dict => "d{" + string.Join(",", dict.OrderBy(pair => pair.Key).Select(pair => pair.Key + ":" + Signature(pair.Value))) + "}",
            _ => value.GetType().Name + value,
        };
    }

    sealed class ByteListComparer : IComparer<byte[]>
    {
        public static readonly ByteListComparer Instance = new();
        public int Compare(byte[]? x, byte[]? y)
        {
            x ??= Array.Empty<byte>();
            y ??= Array.Empty<byte>();
            int n = Math.Min(x.Length, y.Length);
            for (int i = 0; i < n; i++)
            {
                int diff = x[i] - y[i];
                if (diff != 0) return diff;
            }
            return x.Length - y.Length;
        }
    }
}
