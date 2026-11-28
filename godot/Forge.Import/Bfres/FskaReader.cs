namespace OcarinaForge.Import;

/// <summary>One sampled pose. Channels without a curve stay at the bone base (scale 1, translation 0, identity rotation).</summary>
public sealed class ImportedPoseKey
{
    public float Frame { get; init; }
    public float Px { get; init; }
    public float Py { get; init; }
    public float Pz { get; init; }
    public float Rx { get; init; }
    public float Ry { get; init; }
    public float Rz { get; init; }
    public float Rw { get; init; } = 1;
    public float Sx { get; init; } = 1;
    public float Sy { get; init; } = 1;
    public float Sz { get; init; } = 1;
}

public sealed class ImportedBoneTrack
{
    public string Bone { get; init; } = "";
    /// <summary>Skeleton index from the FSKA bind-index table. -1 when the table does not name one.</summary>
    public int Index { get; init; } = -1;
    public List<ImportedPoseKey> Keys { get; init; } = new();
}

public sealed class ImportedClip
{
    public string Name { get; init; } = "";
    public int FrameCount { get; init; }
    public bool Loop { get; init; }
    /// <summary>Rotation curves are Euler XYZ radians. Otherwise they are a quaternion.</summary>
    public bool Euler { get; init; }
    public List<ImportedBoneTrack> Bones { get; init; } = new();
}

/// <summary>
/// Switch FSKA for BFRES major 9 and 10. Longshot <c>has()</c> has no animation getter and
/// <c>content.model</c> does not return curves. The layout is the public v9+ Switch header
/// (BfresLibrary's FSKA reader and the documented curve flags): absolute u64 offsets, flags
/// immediately after the signature, and 16 reserved bytes on each bone track. Version 10 uses
/// that same FSKA header; the v10 file difference is the external GPU section, which this
/// reader does not need. No retail FSKA was in this tree, so a hand-built clip is the check.
/// Material, visibility, and shape curves are not read.
/// </summary>
public static class FskaReader
{
    const int Header = 80;
    const int BoneSize = 56;
    const int CurveSize = 48;
    const int TranslateX = 0x10;
    const int RotateX = 0x20;

    public static List<ImportedClip> Read(byte[] data, int major)
    {
        var clips = new List<ImportedClip>();
        if (major < 9 || data.Length < 0x68) return clips;
        int array = (int)Bin.U64(data, 0x58);
        int dict = (int)Bin.U64(data, 0x60);
        if (array <= 0 || dict <= 0 || array >= data.Length || dict >= data.Length) return clips;
        var names = DictKeys(data, dict);
        for (int index = 0; index < names.Count; index++)
        {
            int at = array + index * Header;
            if (at + Header > data.Length) break;
            if (data[at] != (byte)'F' || data[at + 1] != (byte)'S' || data[at + 2] != (byte)'K' || data[at + 3] != (byte)'A')
                break;
            var clip = Clip(data, at, names[index]);
            if (clip is not null) clips.Add(clip);
        }
        return clips;
    }

    static ImportedClip? Clip(byte[] data, int at, string dictName)
    {
        uint flags = Bin.U32(data, at + 4);
        string name = Text(data, (int)Bin.U64(data, at + 8));
        if (name.Length == 0) name = dictName;
        if (name.Length == 0) name = "clip";
        int bindAt = (int)Bin.U64(data, at + 0x20);
        int boneAt = (int)Bin.U64(data, at + 0x28);
        int frames = Bin.I32(data, at + 0x40);
        int boneCount = Bin.U16(data, at + 0x4C);
        if (boneCount is <= 0 or > 4096 || boneAt <= 0 || boneAt >= data.Length) return null;
        if (frames < 0) frames = 0;
        if (frames > 100000) frames = 100000;
        bool euler = ((flags >> 12) & 7) == 1;
        bool loop = (flags & 4) != 0;
        var bones = new List<ImportedBoneTrack>();
        for (int index = 0; index < boneCount; index++)
        {
            int bone = boneAt + index * BoneSize;
            if (bone + BoneSize > data.Length) break;
            var track = Bone(data, bone, bindAt, index, euler);
            if (track is not null) bones.Add(track);
        }
        if (bones.Count == 0) return null;
        return new ImportedClip
        {
            Name = name,
            FrameCount = frames,
            Loop = loop,
            Euler = euler,
            Bones = bones,
        };
    }

    static ImportedBoneTrack? Bone(byte[] data, int at, int bindAt, int index, bool euler)
    {
        string name = Text(data, (int)Bin.U64(data, at));
        int curveAt = (int)Bin.U64(data, at + 8);
        int baseAt = (int)Bin.U64(data, at + 16);
        uint flags = Bin.U32(data, at + 40);
        int curves = data[at + 46];
        int bind = -1;
        if (bindAt > 0 && bindAt + (index + 1) * 2 <= data.Length)
            bind = Bin.U16(data, bindAt + index * 2);
        var pose = Base(data, baseAt, flags);
        var parsed = new List<Curve>();
        if (curveAt > 0 && curves > 0)
        {
            for (int i = 0; i < curves; i++)
            {
                int cursor = curveAt + i * CurveSize;
                if (cursor + CurveSize > data.Length) break;
                var curve = ReadCurve(data, cursor);
                if (curve is not null) parsed.Add(curve);
            }
        }
        if (parsed.Count == 0 && name.Length == 0) return null;
        var times = new SortedSet<float>();
        foreach (var curve in parsed)
        {
            for (int i = 0; i < curve.Frames.Length; i++)
            {
                times.Add(curve.Frames[i]);
                if (curve.Kind == 0 && i + 1 < curve.Frames.Length)
                    times.Add((curve.Frames[i] + curve.Frames[i + 1]) * 0.5f);
            }
        }
        if (times.Count == 0) times.Add(0);
        var keys = new List<ImportedPoseKey>();
        foreach (float time in times)
        {
            if (keys.Count >= 4096) break;
            float px = pose.Px, py = pose.Py, pz = pose.Pz;
            float rx = pose.Rx, ry = pose.Ry, rz = pose.Rz, rw = pose.Rw;
            float sx = pose.Sx, sy = pose.Sy, sz = pose.Sz;
            foreach (var curve in parsed)
            {
                float value = curve.At(time);
                switch (curve.Target)
                {
                    case 0x04: sx = value; break;
                    case 0x08: sy = value; break;
                    case 0x0C: sz = value; break;
                    case TranslateX: px = value; break;
                    case 0x14: py = value; break;
                    case 0x18: pz = value; break;
                    case RotateX: rx = value; break;
                    case 0x24: ry = value; break;
                    case 0x28: rz = value; break;
                    case 0x2C: rw = value; break;
                }
            }
            if (euler)
            {
                var quat = BindPose.Euler(rx, ry, rz);
                rx = (float)quat.X;
                ry = (float)quat.Y;
                rz = (float)quat.Z;
                rw = (float)quat.W;
            }
            keys.Add(new ImportedPoseKey
            {
                Frame = time,
                Px = px, Py = py, Pz = pz,
                Rx = rx, Ry = ry, Rz = rz, Rw = rw,
                Sx = sx, Sy = sy, Sz = sz,
            });
        }
        return new ImportedBoneTrack { Bone = name.Length == 0 ? "bone_" + index : name, Index = bind, Keys = keys };
    }

    static (float Px, float Py, float Pz, float Rx, float Ry, float Rz, float Rw, float Sx, float Sy, float Sz) Base(byte[] data, int at, uint flags)
    {
        float sx = 1, sy = 1, sz = 1, rx = 0, ry = 0, rz = 0, rw = 1, px = 0, py = 0, pz = 0;
        if (at <= 0 || at >= data.Length) return (px, py, pz, rx, ry, rz, rw, sx, sy, sz);
        int cursor = at;
        if ((flags & (1 << 3)) != 0 && cursor + 12 <= data.Length)
        {
            sx = Bin.F32(data, cursor);
            sy = Bin.F32(data, cursor + 4);
            sz = Bin.F32(data, cursor + 8);
            cursor += 12;
        }
        if ((flags & (1 << 4)) != 0 && cursor + 16 <= data.Length)
        {
            rx = Bin.F32(data, cursor);
            ry = Bin.F32(data, cursor + 4);
            rz = Bin.F32(data, cursor + 8);
            rw = Bin.F32(data, cursor + 12);
            cursor += 16;
        }
        if ((flags & (1 << 5)) != 0 && cursor + 12 <= data.Length)
        {
            px = Bin.F32(data, cursor);
            py = Bin.F32(data, cursor + 4);
            pz = Bin.F32(data, cursor + 8);
        }
        return (px, py, pz, rx, ry, rz, rw, sx, sy, sz);
    }

    sealed class Curve
    {
        public int Kind;
        public int Target;
        public float Scale = 1;
        public float Offset;
        public float[] Frames = Array.Empty<float>();
        public float[][] Keys = Array.Empty<float[]>();

        public float At(float frame)
        {
            if (Frames.Length == 0 || Keys.Length == 0) return Offset;
            if (frame <= Frames[0] || Frames.Length == 1) return Value(0);
            int hi = 1;
            while (hi < Frames.Length && Frames[hi] < frame) hi++;
            if (hi >= Frames.Length) return Value(Frames.Length - 1);
            int lo = hi - 1;
            float span = Frames[hi] - Frames[lo];
            if (span <= 1e-6f || Kind is 4 or 6) return Value(lo);
            float t = (frame - Frames[lo]) / span;
            float p0 = Value(lo);
            float p1 = Value(hi);
            if (Kind != 0) return p0 + (p1 - p0) * t;
            float m0 = Slope(lo) * span;
            float m1 = SlopeIn(hi) * span;
            float t2 = t * t;
            float t3 = t2 * t;
            return (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * m1;
        }

        float Value(int index) => Offset + Keys[index][0] * Scale;
        float Slope(int index) => Keys[index].Length > 1 ? Keys[index][1] * Scale : 0;
        float SlopeIn(int index) => Keys[index].Length > 2 ? Keys[index][2] * Scale : Slope(index);
    }

    static Curve? ReadCurve(byte[] data, int at)
    {
        int frameAt = (int)Bin.U64(data, at);
        int keyAt = (int)Bin.U64(data, at + 8);
        int flags = Bin.U16(data, at + 16);
        int count = Bin.U16(data, at + 18);
        if (count is <= 0 or > 100000 || frameAt <= 0 || keyAt <= 0) return null;
        int frameType = flags & 3;
        int keyType = (flags >> 2) & 3;
        int kind = (flags >> 4) & 7;
        int elements = kind switch { 0 => 4, 1 => 2, _ => 1 };
        int frameStep = frameType == 0 ? 4 : 2;
        if (frameType == 2) frameStep = 1;
        int keyStep = keyType switch { 0 => 4, 1 => 2, 2 => 1, _ => 0 };
        if (keyStep == 0) return null;
        if ((long)frameAt + (long)count * frameStep > data.Length) return null;
        if ((long)keyAt + (long)count * elements * keyStep > data.Length) return null;
        var frames = new float[count];
        var keys = new float[count][];
        for (int i = 0; i < count; i++)
        {
            frames[i] = Frame(data, frameAt + i * frameStep, frameType);
            var row = new float[elements];
            for (int e = 0; e < elements; e++)
                row[e] = Key(data, keyAt + (i * elements + e) * keyStep, keyType);
            keys[i] = row;
        }
        float scale = Bin.F32(data, at + 32);
        if (scale == 0) scale = 1;
        return new Curve
        {
            Kind = kind,
            Target = (int)Bin.U32(data, at + 20),
            Scale = scale,
            Offset = Bin.F32(data, at + 36),
            Frames = frames,
            Keys = keys,
        };
    }

    static float Frame(byte[] data, int at, int kind)
    {
        if (kind == 0) return Bin.F32(data, at);
        if (kind == 2) return data[at];
        return Bin.I16(data, at) / 32f;
    }

    static float Key(byte[] data, int at, int kind)
    {
        if (kind == 0) return Bin.F32(data, at);
        if (kind == 2) return (sbyte)data[at];
        return Bin.I16(data, at);
    }

    static string Text(byte[] data, int at) => Bin.LengthString(data, at);

    static List<string> DictKeys(byte[] data, int at)
    {
        if (at + 8 > data.Length) return new List<string>();
        int count = Bin.I32(data, at + 4);
        if (count is <= 0 or > 100000) return new List<string>();
        var keys = new List<string>(count);
        for (int index = 1; index <= count; index++)
        {
            int node = at + 8 + index * 16;
            if (node + 16 > data.Length) break;
            keys.Add(Bin.LengthString(data, (int)Bin.U64(data, node + 8)));
        }
        return keys;
    }
}
