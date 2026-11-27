using System.Text.RegularExpressions;

namespace OcarinaForge.Import;

/// <summary>Albedo slot choice from <c>formats/bfres.py</c>. <c>_a0</c> wins over a normal bound beside it.</summary>
public static class AlbedoRank
{
    static readonly HashSet<string> Primary = new(StringComparer.Ordinal)
    {
        "a0", "alb", "alb0", "albedo", "albedo0", "basecolor", "basecolor0", "diffuse", "diffuse0",
        "d0", "col0", "bc0", "color0", "base", "color",
    };
    static readonly HashSet<string> Extra = new(StringComparer.Ordinal) { "a1", "a2", "alb1", "alb2", "albedo1", "albedo2" };
    static readonly HashSet<string> SkipTokens = new(StringComparer.Ordinal)
    {
        "nrm", "nml", "normal", "norm", "spm", "spc", "spec", "rgh", "rough", "emm", "emit", "mtl",
        "orm", "msk", "mask", "blend", "bake", "gn", "splat", "height", "fx", "ind",
    };
    static readonly HashSet<string> AlbedoTokens = new(StringComparer.Ordinal)
    {
        "alb", "albedo", "basecolor", "diffuse", "diff", "paint", "color",
    };
    static readonly Regex SamplerSkip = new(
        @"^(n|n[0-7]|nrm[0-7]?|normal[0-7]?|s[0-7]|spc[0-7]?|spec[0-7]?|r[0-7]|rgh[0-7]?|rough[0-7]?|m[0-7]|mtl[0-7]?|metal[0-7]?|e[0-7]|emm[0-7]?|emit[0-7]?|b[0-7]|blend[0-7]?|splat[0-7]?|ao[0-7]?|mask[0-7]?|orm[0-7]?|h[0-7]|height[0-7]?|bake[0-7]?|lightmap[0-7]?)$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public static List<(string Sampler, string Texture)> Rank(IReadOnlyList<(string Sampler, string Texture)> binds)
    {
        if (binds.Any(item => item.Sampler.Length > 0))
        {
            var locked = RankLocked(binds);
            if (locked.Count > 0) return locked;
            return RankNamed(binds);
        }
        var ranked = new List<(int A, int B, int C, string Sampler, string Texture)>();
        for (int index = 0; index < binds.Count; index++)
        {
            var (sampler, texture) = binds[index];
            if (texture.Length == 0) continue;
            int? srank = SamplerRank(sampler);
            int trank = TextureRank(texture);
            if (srank == 9 || (srank is null && trank == 2)) continue;
            var key = srank == 0 ? (0, trank, index) : srank == 1 ? (1, trank, index) : trank == 0 ? (2, 0, index) : (3, trank, index);
            ranked.Add((key.Item1, key.Item2, key.Item3, sampler, texture));
        }
        ranked.Sort((left, right) => left.A != right.A ? left.A - right.A : left.B != right.B ? left.B - right.B : left.C - right.C);
        return ranked.Select(item => (item.Sampler, item.Texture)).ToList();
    }

    static List<(string, string)> RankLocked(IReadOnlyList<(string Sampler, string Texture)> binds)
    {
        var ranked = new List<(int Key, int Index, string Sampler, string Texture)>();
        for (int index = 0; index < binds.Count; index++)
        {
            var (sampler, texture) = binds[index];
            if (texture.Length == 0) continue;
            string token = Letters(sampler);
            if (TextureRank(texture) >= 2 && !(Extra.Contains(token) && OverlayAlbedo(texture))) continue;
            int key = Primary.Contains(token) ? 0 : Extra.Contains(token) ? 1 : -1;
            if (key < 0) continue;
            ranked.Add((key, index, sampler, texture));
        }
        ranked.Sort((a, b) => a.Key != b.Key ? a.Key - b.Key : a.Index - b.Index);
        return ranked.Select(item => (item.Sampler, item.Texture)).ToList();
    }

    static List<(string, string)> RankNamed(IReadOnlyList<(string Sampler, string Texture)> binds)
    {
        var found = new List<(int A, int B, int C, string Sampler, string Texture)>();
        for (int index = 0; index < binds.Count; index++)
        {
            var (sampler, texture) = binds[index];
            if (texture.Length == 0) continue;
            string token = Letters(sampler);
            if (token is "tc" or "ta" || token.StartsWith("material", StringComparison.Ordinal)) continue;
            if (SamplerRank(sampler) == 9) continue;
            if (Regex.IsMatch(texture.Split('/').Last(), @"^material(?:alb|cmb)\d*$", RegexOptions.IgnoreCase)) continue;
            int trank = TextureRank(texture);
            bool named = SaysAlbedo(token);
            if (trank == 0 || (named && trank < 2)) found.Add((named ? 0 : 1, trank, index, sampler, texture));
            else if (trank < 2 && SamplerRank(sampler) is null && token.Length > 0 && !BakeTexture(texture))
                found.Add((2, trank, index, sampler, texture));
        }
        found.Sort((a, b) => a.A != b.A ? a.A - b.A : a.B != b.B ? a.B - b.B : a.C - b.C);
        return found.Select(item => (item.Sampler, item.Texture)).ToList();
    }

    public static int TextureRank(string name)
    {
        string stem = name.ToLowerInvariant().Split('/').Last();
        var bag = Tokens(stem);
        if (BakeTexture(name) || bag.Overlaps(SkipTokens) || bag.Any(item => item.StartsWith("nrm", StringComparison.Ordinal) || item.StartsWith("normal", StringComparison.Ordinal)))
            return 2;
        if (stem.Length > 2 && (stem.EndsWith("fx", StringComparison.Ordinal) || stem.EndsWith("msk", StringComparison.Ordinal) || stem.EndsWith("nrm", StringComparison.Ordinal)))
            return 2;
        if (bag.Overlaps(AlbedoTokens) || bag.Any(item => item.StartsWith("alb", StringComparison.Ordinal))) return 0;
        return 1;
    }

    static bool BakeTexture(string name)
    {
        string stem = name.ToLowerInvariant().Split('/').Last();
        if (stem.Contains("cmntex_bake", StringComparison.Ordinal) || stem.Contains("bakelight", StringComparison.Ordinal) || stem.Contains("bake_light", StringComparison.Ordinal))
            return true;
        return Tokens(stem).Any(item => item == "bake" || item.StartsWith("bakedefault", StringComparison.Ordinal) || item.StartsWith("bakelight", StringComparison.Ordinal) || item.StartsWith("lightmap", StringComparison.Ordinal));
    }

    static bool OverlayAlbedo(string texture)
    {
        string stem = texture.ToLowerInvariant();
        if (stem.Contains("nrm") || stem.Contains("normal") || stem.Contains("mask") || stem.Contains("msk") || stem.Contains("bake") || stem.Contains("dummy"))
            return false;
        var tokens = Tokens(stem);
        return !tokens.Contains("gn") && !tokens.Contains("bake");
    }

    static bool SaysAlbedo(string token) =>
        token.Length > 0 && (Primary.Contains(token) || Extra.Contains(token) || token.StartsWith("alb", StringComparison.Ordinal)
            || token.Contains("albedo", StringComparison.Ordinal) || token.Contains("basecolor", StringComparison.Ordinal) || token.Contains("diffuse", StringComparison.Ordinal));

    static int? SamplerRank(string name)
    {
        string token = Letters(name);
        if (token.Length == 0) return null;
        if (Primary.Contains(token)) return 0;
        if (Extra.Contains(token) || (token.Length == 2 && token[0] == 'a' && token[1] is >= '1' and <= '7')) return 1;
        if (SamplerSkip.IsMatch(token)) return 9;
        return null;
    }

    static string Letters(string name)
    {
        var chars = new List<char>(name.Length);
        foreach (char item in name.ToLowerInvariant())
            if (char.IsLetterOrDigit(item)) chars.Add(item);
        return new string(chars.ToArray());
    }

    static HashSet<string> Tokens(string stem)
    {
        var bag = new HashSet<string>(StringComparer.Ordinal);
        var token = new List<char>();
        void Flush()
        {
            if (token.Count == 0) return;
            bag.Add(new string(token.ToArray()));
            token.Clear();
        }
        foreach (char item in stem)
        {
            if (char.IsLetterOrDigit(item)) token.Add(item);
            else Flush();
        }
        Flush();
        return bag;
    }
}
