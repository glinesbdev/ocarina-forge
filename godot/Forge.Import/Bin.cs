using System.Buffers.Binary;
using System.Text;

namespace OcarinaForge.Import;

static class Bin
{
    public static ushort U16(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadUInt16LittleEndian(data.Slice(at));
    public static short I16(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadInt16LittleEndian(data.Slice(at));
    public static uint U32(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadUInt32LittleEndian(data.Slice(at));
    public static int I32(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadInt32LittleEndian(data.Slice(at));
    public static ulong U64(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadUInt64LittleEndian(data.Slice(at));
    public static long I64(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadInt64LittleEndian(data.Slice(at));
    public static float F32(ReadOnlySpan<byte> data, int at) => BinaryPrimitives.ReadSingleLittleEndian(data.Slice(at));

    public static float F16(ReadOnlySpan<byte> data, int at) =>
        (float)BitConverter.UInt16BitsToHalf(U16(data, at));

    public static int Rel32(ReadOnlySpan<byte> data, int at)
    {
        if ((uint)at > (uint)(data.Length - 4))
            throw new ImportException($"relative offset at 0x{at:X} is outside the file");
        return at + (int)U32(data, at);
    }

    public static string CString(ReadOnlySpan<byte> data, int at, int max)
    {
        if (at < 0 || at >= data.Length) return "";
        int end = at;
        int limit = Math.Min(data.Length, at + max);
        while (end < limit && data[end] != 0) end++;
        return Encoding.UTF8.GetString(data.Slice(at, end - at));
    }

    /// <summary>BFRES / BNTX string: u16 length, bytes, NUL. <paramref name="at"/> points at the length.</summary>
    public static string LengthString(ReadOnlySpan<byte> data, int at)
    {
        if (at <= 0 || at + 2 > data.Length) return "";
        int length = U16(data, at);
        int end = at + 2 + length;
        if (length > 1024 || end > data.Length) return "";
        return Encoding.UTF8.GetString(data.Slice(at + 2, length));
    }

    public static bool IsLengthString(ReadOnlySpan<byte> data, int at)
    {
        if (at <= 0 || at + 3 > data.Length) return false;
        int length = U16(data, at);
        if (length > 160) return false;
        int end = at + 2 + length;
        if (end >= data.Length || data[end] != 0) return false;
        for (int i = at + 2; i < end; i++)
        {
            if (data[i] > 127) return false;
        }
        return true;
    }
}

public class ImportException : Exception
{
    public ImportException(string message) : base(message) { }
    public ImportException(string message, Exception inner) : base(message, inner) { }
}
