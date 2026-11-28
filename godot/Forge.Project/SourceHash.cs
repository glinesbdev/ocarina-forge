using System.Security.Cryptography;
using System.Text;

namespace OcarinaForge.Project;

/// <summary>SHA-256 of the importer version plus the raw bytes of each romfs file. A pack member hashes the archive.</summary>
public static class SourceHash
{
    public static string Of(DumpFolder dump, IEnumerable<string> sources)
    {
        using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        Add(sha, ProjectPlan.ImporterVersion);
        foreach (string source in sources.Where(item => item.Length > 0).Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal))
        {
            Add(sha, source);
            string file = source.Split('#')[0];
            string path = Path.Combine(dump.Romfs, file.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(path))
            {
                Add(sha, "missing");
                continue;
            }
            Add(sha, new FileInfo(path).Length.ToString());
            using var stream = File.OpenRead(path);
            var buffer = new byte[128 * 1024];
            int read;
            while ((read = stream.Read(buffer)) > 0)
                sha.AppendData(buffer.AsSpan(0, read));
        }
        return Convert.ToHexString(sha.GetHashAndReset()).ToLowerInvariant();
    }

    static void Add(IncrementalHash sha, string text)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(text);
        sha.AppendData(BitConverter.GetBytes(bytes.Length));
        sha.AppendData(bytes);
    }
}
