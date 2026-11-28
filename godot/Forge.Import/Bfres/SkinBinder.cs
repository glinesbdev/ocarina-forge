namespace OcarinaForge.Import;

/// <summary>
/// Picks the bone-index space that keeps a posed mesh near its rest size, then bakes rigid vertices
/// into that space. Smooth vertices stay in model space. Godot's formula is
/// <c>poseWorld * inverse(restWorld) * vertex</c>, so a wrong index looks fine at the bind pose
/// and explodes as soon as a clip moves the bone it actually points at.
/// BfresLibrary stores the FSKL matrix list as smooth entries first (<c>+0x3A</c>) and rigid entries
/// after them (<c>+0x3C</c>). FSHP keeps its own skin-bone table. Vertex <c>_i0</c> is one of those
/// slots, not always a skeleton bone. The web viewer never posed smooth skin, so this only shows up
/// once Godot plays a clip.
/// </summary>
public static class SkinBinder
{
    const double Grow = 3.0;

    public static void Apply(ImportedModel model)
    {
        if (model.Skeleton.Count == 0) return;
        bool pending = model.Clips.Any(clip => !clip.KeysCompensated);
        if (model.SkinBound && !pending) return;
        Remember(model);
        Compensate(model);
        string mapping = Choose(model);
        Bind(model, mapping);
        model.SkinMapping = mapping;
        model.SkinBound = true;
    }

    /// <summary>Largest posed-extent / rest-extent ratio across the sampled clips. 1 is a rigid motion.</summary>
    public static double WorstExtentRatio(ImportedModel model)
    {
        if (model.Skeleton.Count == 0) return 1;
        var rest = RestWorlds(model);
        var inverse = rest.Select(BindPose.Invert).ToList();
        double worst = 1;
        foreach (var clip in SampleClips(model))
        {
            foreach (float frame in Frames(clip))
            {
                var pose = PoseWorlds(model, clip, frame);
                foreach (var part in model.Parts)
                {
                    if (part.Weights is not { Count: > 0 } || part.Positions.Count == 0) continue;
                    double ratio = Ratio(part.Positions, part.Weights, inverse, pose);
                    if (ratio > worst) worst = ratio;
                }
            }
        }
        return worst;
    }

    static void Remember(ImportedModel model)
    {
        foreach (var part in model.Parts)
        {
            part.LocalPositions ??= part.Positions.ToList();
            if (part.LocalNormals is null && part.Normals is not null)
                part.LocalNormals = part.Normals.ToList();
            if (part.RawWeights is null && part.Weights is not null)
                part.RawWeights = part.Weights.ToList();
        }
    }

    static void Compensate(ImportedModel model)
    {
        foreach (var clip in model.Clips)
        {
            if (clip.KeysCompensated) continue;
            var raw = new Dictionary<int, List<ImportedPoseKey>>();
            foreach (var track in clip.Bones)
            {
                track.RawKeys ??= track.Keys.Select(Clone).ToList();
                int index = BoneIndex(model.Skeleton, track);
                if (index >= 0) raw[index] = track.RawKeys;
            }
            foreach (var track in clip.Bones)
            {
                int index = BoneIndex(model.Skeleton, track);
                if (index < 0 || track.RawKeys is null) continue;
                var bone = model.Skeleton[index];
                if (!bone.ScaleCompensate)
                {
                    track.Keys = track.RawKeys.Select(Clone).ToList();
                    continue;
                }
                var rewritten = new List<ImportedPoseKey>(track.RawKeys.Count);
                foreach (var key in track.RawKeys)
                {
                    var (sx, sy, sz) = ParentScale(model.Skeleton, raw, bone.Parent, key.Frame);
                    var matrix = BindPose.Compensate(key.Px, key.Py, key.Pz, key.Rx, key.Ry, key.Rz, key.Rw, key.Sx, key.Sy, key.Sz, sx, sy, sz, true);
                    rewritten.Add(From(key.Frame, BindPose.Decompose(matrix)));
                }
                track.Keys = rewritten;
            }
            clip.KeysCompensated = true;
        }
        if (model.Skeleton.All(bone => bone.RestCompensated || !bone.ScaleCompensate))
        {
            foreach (var bone in model.Skeleton) bone.RestCompensated = true;
            return;
        }
        var worlds = BindPose.Worlds(model.Skeleton.Select(AsBone).ToList());
        for (int i = 0; i < model.Skeleton.Count; i++)
        {
            var local = worlds[i];
            int parent = model.Skeleton[i].Parent;
            if (parent >= 0 && parent < worlds.Count && parent != i)
                local = BindPose.Multiply(BindPose.Invert(worlds[parent]), worlds[i]);
            Write(model.Skeleton[i], BindPose.Decompose(local));
            model.Skeleton[i].RestCompensated = true;
        }
    }

    static string Choose(ImportedModel model)
    {
        if (!model.Parts.Any(part => part.SkinCount >= 1 && part.RawWeights is { Count: > 0 }))
            return "palette";
        var clips = SampleClips(model).ToList();
        if (clips.Count == 0) return "palette";
        var names = new List<string> { "palette", "direct" };
        if (model.Parts.Any(part => part.ShapeBones.Count > 0))
        {
            names.Add("shape");
            names.Add("shape-direct");
        }
        if (model.RigidMatrices > 0) names.Add("rigid-tail");
        string best = "palette";
        double bestScore = double.PositiveInfinity;
        double palette = double.PositiveInfinity;
        double rigid = double.PositiveInfinity;
        foreach (string name in names)
        {
            double score = Score(model, name, clips);
            if (name == "palette") palette = score;
            if (name == "rigid-tail") rigid = score;
            if (score < bestScore)
            {
                bestScore = score;
                best = name;
            }
        }
        // Skin count 1 indexes the rigid half, which starts after the smooth entries.
        // Smooth parts under this mapping still use the front of the palette.
        if (rigid <= bestScore * 1.2) return "rigid-tail";
        if (double.IsPositiveInfinity(palette) || palette <= bestScore * 1.2) return "palette";
        return best;
    }

    static double Score(ImportedModel model, string mapping, List<ImportedClip> clips)
    {
        var rest = RestWorlds(model);
        var inverse = rest.Select(BindPose.Invert).ToList();
        int bones = model.Skeleton.Count;
        int misses = 0;
        double worst = 1;
        var restPoints = new List<Vec3>();
        var samples = new List<(Vec3 Local, VertexSkin Mapped, bool ModelSpace)>();
        foreach (var part in model.Parts)
        {
            if (part.SkinCount < 1 || part.RawWeights is not { Count: > 0 } || part.LocalPositions is not { Count: > 0 })
                continue;
            int count = Math.Min(part.RawWeights.Count, part.LocalPositions.Count);
            int stride = Math.Max(1, count / 400);
            for (int i = 0; i < count; i += stride)
            {
                var mapped = MapSkin(model, part, part.RawWeights[i], mapping);
                if (Misses(mapped, bones)) misses++;
                bool modelSpace = part.SkinCount >= 2 || !RigidSlot(model, mapped);
                var local = part.LocalPositions[i];
                samples.Add((local, mapped, modelSpace));
                restPoints.Add(modelSpace ? local : Skinned(local, mapped, rest, null));
            }
        }
        if (misses > 0) worst += 100 + misses;
        double gap = 0;
        int gaps = 0;
        for (int i = 0; i < samples.Count && i < restPoints.Count; i++)
        {
            int bone = samples[i].Mapped.A;
            if ((uint)bone >= (uint)rest.Count) continue;
            var origin = new Vec3(rest[bone][3], rest[bone][7], rest[bone][11]);
            var point = restPoints[i];
            double dx = point.X - origin.X, dy = point.Y - origin.Y, dz = point.Z - origin.Z;
            gap += Math.Sqrt(dx * dx + dy * dy + dz * dz);
            gaps++;
        }
        if (gaps > 0) worst += Math.Max(0, gap / gaps - 0.75);
        foreach (var clip in clips)
        {
            foreach (float frame in Frames(clip))
            {
                var pose = PoseWorlds(model, clip, frame);
                var posed = new List<Vec3>(samples.Count);
                foreach (var (local, mapped, modelSpace) in samples)
                    posed.Add(modelSpace ? Skinned(local, mapped, pose, inverse) : Skinned(local, mapped, pose, null));
                double ratio = ExtentRatio(restPoints, posed);
                if (ratio > worst) worst = ratio;
            }
        }
        return worst;
    }

    static void Bind(ImportedModel model, string mapping)
    {
        model.SkinFindings.Clear();
        var bones = model.Skeleton.Select(AsBone).ToList();
        int limit = model.Skeleton.Count;
        foreach (var part in model.Parts)
        {
            if (part.LocalPositions is { Count: > 0 })
            {
                part.Positions.Clear();
                part.Positions.AddRange(part.LocalPositions);
            }
            if (part.LocalNormals is { Count: > 0 })
            {
                part.Normals ??= new List<Vec3>();
                part.Normals.Clear();
                part.Normals.AddRange(part.LocalNormals);
            }
            if (part.RawWeights is not { Count: > 0 }) continue;
            var mapped = new List<VertexSkin>(part.RawWeights.Count);
            int reported = 0;
            int extra = 0;
            foreach (var skin in part.RawWeights)
            {
                var next = MapSkin(model, part, skin, mapping);
                mapped.Add(new VertexSkin(
                    ClampOne(model, part, next.A, next.Wa, limit, ref reported, ref extra),
                    ClampOne(model, part, next.B, next.Wb, limit, ref reported, ref extra),
                    ClampOne(model, part, next.C, next.Wc, limit, ref reported, ref extra),
                    ClampOne(model, part, next.D, next.Wd, limit, ref reported, ref extra),
                    next.Wa, next.Wb, next.Wc, next.Wd,
                    ClampOne(model, part, next.E, next.We, limit, ref reported, ref extra),
                    ClampOne(model, part, next.F, next.Wf, limit, ref reported, ref extra),
                    ClampOne(model, part, next.G, next.Wg, limit, ref reported, ref extra),
                    ClampOne(model, part, next.H, next.Wh, limit, ref reported, ref extra),
                    next.We, next.Wf, next.Wg, next.Wh));
            }
            if (extra > 0)
                model.SkinFindings.Add($"{model.Name} {part.Name}: {extra} more bone indices are outside 0..{limit - 1}");
            part.Weights = mapped;
            if (part.SkinCount > 1 || part.Positions.Count == 0) continue;
            var slots = part.SkinCount == 1
                ? mapped.Select(skin => skin.A).ToList()
                : null;
            int shapeBone = mapped.Count > 0 ? mapped[0].A : 0;
            Bake(part.Positions, bones, shapeBone, slots);
            if (part.Normals is { Count: > 0 })
                BindPose.BakeNormals(part.Normals, bones, Array.Empty<int>(), shapeBone, slots);
        }
        double ratio = WorstExtentRatio(model);
        if (ratio > Grow)
            model.SkinFindings.Add($"{model.Name}: posed bounds are {ratio:0.00}x the rest bounds ({mapping})");
        double gap = RestGap(model);
        if (gap > 1.25)
            model.SkinFindings.Add($"{model.Name}: rest vertices sit {gap:0.00}m from their bones ({mapping})");
    }

    static double RestGap(ImportedModel model)
    {
        var rest = RestWorlds(model);
        double gap = 0;
        int count = 0;
        foreach (var part in model.Parts)
        {
            if (part.Weights is not { Count: > 0 }) continue;
            int n = Math.Min(part.Weights.Count, part.Positions.Count);
            int stride = Math.Max(1, n / 400);
            for (int i = 0; i < n; i += stride)
            {
                int bone = part.Weights[i].A;
                if ((uint)bone >= (uint)rest.Count || part.Weights[i].Wa <= 0) continue;
                var origin = new Vec3(rest[bone][3], rest[bone][7], rest[bone][11]);
                var point = part.Positions[i];
                double dx = point.X - origin.X, dy = point.Y - origin.Y, dz = point.Z - origin.Z;
                gap += Math.Sqrt(dx * dx + dy * dy + dz * dz);
                count++;
            }
        }
        return count == 0 ? 0 : gap / count;
    }

    static int ClampOne(ImportedModel model, ImportedPart part, int index, float weight, int limit, ref int reported, ref int extra)
    {
        if (weight <= 0 || limit <= 0) return index;
        if ((uint)index < (uint)limit) return index;
        if (reported < 8)
        {
            model.SkinFindings.Add($"{model.Name} {part.Name}: bone index {index} is outside 0..{limit - 1}");
            reported++;
        }
        else extra++;
        return 0;
    }

    static void Bake(List<Vec3> positions, List<BindPose.Bone> bones, int boneIndex, List<int>? slots)
    {
        var raw = positions.Select(item => (item.X, item.Y, item.Z)).ToList();
        BindPose.Bake(raw, bones, Array.Empty<int>(), boneIndex, slots);
        for (int i = 0; i < positions.Count; i++)
            positions[i] = new Vec3(raw[i].X, raw[i].Y, raw[i].Z);
    }

    static VertexSkin MapSkin(ImportedModel model, ImportedPart part, VertexSkin skin, string mapping)
    {
        if (part.SkinCount <= 0) return skin;
        return new VertexSkin(
            Map(model, part, skin.A, mapping), Map(model, part, skin.B, mapping),
            Map(model, part, skin.C, mapping), Map(model, part, skin.D, mapping),
            skin.Wa, skin.Wb, skin.Wc, skin.Wd,
            Map(model, part, skin.E, mapping), Map(model, part, skin.F, mapping),
            Map(model, part, skin.G, mapping), Map(model, part, skin.H, mapping),
            skin.We, skin.Wf, skin.Wg, skin.Wh);
    }

    static int Map(ImportedModel model, ImportedPart part, int raw, string mapping)
    {
        int slot = raw;
        if (mapping is "shape" or "shape-direct" && part.ShapeBones.Count > 0 && raw >= 0 && raw < part.ShapeBones.Count)
            slot = part.ShapeBones[raw];
        if (mapping is "direct" or "shape-direct") return slot;
        var palette = model.MatrixPalette;
        if (mapping == "rigid-tail" && part.SkinCount <= 1 && model.RigidMatrices > 0 && slot >= 0 && slot < model.RigidMatrices)
        {
            int at = model.SmoothMatrices + slot;
            if (at >= 0 && at < palette.Count) return palette[at];
        }
        if (palette.Count > 0 && slot >= 0 && slot < palette.Count) return palette[slot];
        return slot;
    }

    static bool RigidSlot(ImportedModel model, VertexSkin skin)
    {
        int index = skin.A;
        return index >= 0 && index < model.Skeleton.Count && model.Skeleton[index].Rigid >= 0;
    }

    static bool Misses(VertexSkin skin, int bones)
    {
        bool Bad(int index, float weight) => weight > 0 && ((uint)index >= (uint)bones);
        return Bad(skin.A, skin.Wa) || Bad(skin.B, skin.Wb) || Bad(skin.C, skin.Wc) || Bad(skin.D, skin.Wd)
            || Bad(skin.E, skin.We) || Bad(skin.F, skin.Wf) || Bad(skin.G, skin.Wg) || Bad(skin.H, skin.Wh);
    }

    static double Ratio(List<Vec3> positions, List<VertexSkin> weights, List<double[]> inverse, List<double[]> pose)
    {
        int count = Math.Min(positions.Count, weights.Count);
        int stride = Math.Max(1, count / 400);
        var restPoints = new List<Vec3>();
        var posed = new List<Vec3>();
        for (int i = 0; i < count; i += stride)
        {
            restPoints.Add(positions[i]);
            posed.Add(Skinned(positions[i], weights[i], pose, inverse));
        }
        return ExtentRatio(restPoints, posed);
    }

    static Vec3 Skinned(Vec3 point, VertexSkin skin, List<double[]> worlds, List<double[]>? inverse)
    {
        Span<int> ids = stackalloc int[8] { skin.A, skin.B, skin.C, skin.D, skin.E, skin.F, skin.G, skin.H };
        Span<float> weights = stackalloc float[8] { skin.Wa, skin.Wb, skin.Wc, skin.Wd, skin.We, skin.Wf, skin.Wg, skin.Wh };
        double x = 0, y = 0, z = 0, total = 0;
        for (int i = 0; i < 8; i++)
        {
            if (weights[i] <= 0) continue;
            int bone = ids[i];
            if ((uint)bone >= (uint)worlds.Count) continue;
            var matrix = inverse is null ? worlds[bone] : BindPose.Multiply(worlds[bone], inverse[bone]);
            var moved = BindPose.Apply(matrix, point.X, point.Y, point.Z);
            x += weights[i] * moved.X;
            y += weights[i] * moved.Y;
            z += weights[i] * moved.Z;
            total += weights[i];
        }
        if (total <= 1e-6) return point;
        return new Vec3(x / total, y / total, z / total);
    }

    static double ExtentRatio(List<Vec3> rest, List<Vec3> posed)
    {
        double restExtent = Extent(rest);
        double posedExtent = Extent(posed);
        if (restExtent < 1e-4) return posedExtent < 1e-3 ? 1 : 10;
        double ratio = posedExtent / restExtent;
        if (ratio > 0 && ratio < 1) ratio = 1 / ratio;
        return ratio;
    }

    static double Extent(List<Vec3> points)
    {
        if (points.Count == 0) return 0;
        double minX = points[0].X, maxX = minX, minY = points[0].Y, maxY = minY, minZ = points[0].Z, maxZ = minZ;
        foreach (var point in points)
        {
            minX = Math.Min(minX, point.X); maxX = Math.Max(maxX, point.X);
            minY = Math.Min(minY, point.Y); maxY = Math.Max(maxY, point.Y);
            minZ = Math.Min(minZ, point.Z); maxZ = Math.Max(maxZ, point.Z);
        }
        double x = maxX - minX, y = maxY - minY, z = maxZ - minZ;
        return Math.Sqrt(x * x + y * y + z * z);
    }

    static List<double[]> RestWorlds(ImportedModel model) => BindPose.Worlds(model.Skeleton.Select(AsBone).ToList());

    static List<double[]> PoseWorlds(ImportedModel model, ImportedClip clip, float frame)
    {
        var bones = model.Skeleton.Select(AsBone).ToList();
        foreach (var track in clip.Bones)
        {
            int index = BoneIndex(model.Skeleton, track);
            var key = KeyAt(track.Keys, frame);
            if (index < 0 || key is null) continue;
            var bone = bones[index];
            bones[index] = bone with
            {
                Px = key.Px, Py = key.Py, Pz = key.Pz,
                Rx = key.Rx, Ry = key.Ry, Rz = key.Rz, Rw = key.Rw,
                Sx = key.Sx == 0 ? 1 : key.Sx, Sy = key.Sy == 0 ? 1 : key.Sy, Sz = key.Sz == 0 ? 1 : key.Sz,
            };
        }
        return BindPose.Worlds(bones);
    }

    static IEnumerable<ImportedClip> SampleClips(ImportedModel model)
    {
        var preferred = model.Clips.Where(clip =>
        {
            string name = clip.Name;
            return name.Contains("Wait", StringComparison.OrdinalIgnoreCase)
                || name.Contains("Idle", StringComparison.OrdinalIgnoreCase)
                || name.Contains("Walk", StringComparison.OrdinalIgnoreCase)
                || name.Contains("Stand", StringComparison.OrdinalIgnoreCase);
        });
        return preferred.Concat(model.Clips).Distinct().Take(6);
    }

    static IEnumerable<float> Frames(ImportedClip clip)
    {
        float end = Math.Max(0, clip.FrameCount - 1);
        if (end <= 0)
        {
            foreach (var track in clip.Bones)
                foreach (var key in track.Keys)
                    if (key.Frame > end) end = key.Frame;
        }
        yield return 0;
        if (end > 0) yield return end * 0.5f;
        if (end > 1) yield return end;
    }

    static int BoneIndex(List<ImportedBone> bones, ImportedBoneTrack track)
    {
        if (track.Index >= 0 && track.Index < bones.Count &&
            (track.Bone.Length == 0 || bones[track.Index].Name.Equals(track.Bone, StringComparison.Ordinal)))
            return track.Index;
        for (int i = 0; i < bones.Count; i++)
            if (bones[i].Name.Equals(track.Bone, StringComparison.Ordinal)) return i;
        return track.Index >= 0 && track.Index < bones.Count ? track.Index : -1;
    }

    static (double X, double Y, double Z) ParentScale(List<ImportedBone> bones, Dictionary<int, List<ImportedPoseKey>> raw, int parent, float frame)
    {
        if (parent < 0 || parent >= bones.Count) return (1, 1, 1);
        if (raw.TryGetValue(parent, out var keys))
        {
            var key = KeyAt(keys, frame);
            if (key is not null)
                return (key.Sx == 0 ? 1 : key.Sx, key.Sy == 0 ? 1 : key.Sy, key.Sz == 0 ? 1 : key.Sz);
        }
        var bone = bones[parent];
        return (bone.BindSx == 0 ? 1 : bone.BindSx, bone.BindSy == 0 ? 1 : bone.BindSy, bone.BindSz == 0 ? 1 : bone.BindSz);
    }

    static ImportedPoseKey? KeyAt(List<ImportedPoseKey> keys, float frame)
    {
        ImportedPoseKey? chosen = null;
        foreach (var key in keys)
        {
            if (key.Frame > frame + 1e-4f) break;
            chosen = key;
        }
        return chosen ?? (keys.Count > 0 ? keys[0] : null);
    }

    static BindPose.Bone AsBone(ImportedBone bone) => new(
        bone.Parent, bone.Px, bone.Py, bone.Pz, bone.Rx, bone.Ry, bone.Rz, bone.Rw,
        bone.Sx, bone.Sy, bone.Sz,
        bone.ScaleCompensate && !bone.RestCompensated ? 0x800000u : 0u,
        bone.Rigid);

    static void Write(ImportedBone bone, BindPose.Decomposed value)
    {
        bone.Px = value.Px; bone.Py = value.Py; bone.Pz = value.Pz;
        bone.Rx = value.Rx; bone.Ry = value.Ry; bone.Rz = value.Rz; bone.Rw = value.Rw;
        bone.Sx = value.Sx; bone.Sy = value.Sy; bone.Sz = value.Sz;
    }

    static ImportedPoseKey From(float frame, BindPose.Decomposed value) => new()
    {
        Frame = frame,
        Px = (float)value.Px, Py = (float)value.Py, Pz = (float)value.Pz,
        Rx = (float)value.Rx, Ry = (float)value.Ry, Rz = (float)value.Rz, Rw = (float)value.Rw,
        Sx = (float)value.Sx, Sy = (float)value.Sy, Sz = (float)value.Sz,
    };

    static ImportedPoseKey Clone(ImportedPoseKey key) => new()
    {
        Frame = key.Frame,
        Px = key.Px, Py = key.Py, Pz = key.Pz,
        Rx = key.Rx, Ry = key.Ry, Rz = key.Rz, Rw = key.Rw,
        Sx = key.Sx, Sy = key.Sy, Sz = key.Sz,
    };
}
