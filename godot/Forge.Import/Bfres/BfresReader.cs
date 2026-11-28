using System.Text.RegularExpressions;

namespace OcarinaForge.Import;

/// <summary>
/// BFRES reader for the version this dump uses (Switch, little-endian, major 9 and 10).
/// Version 10 has no reserved qword between the material array and its dictionary,
/// and FMAT is 0xB0. This is a port of <c>ocarina_forge/formats/bfres.py</c>.
/// Vertex format ids are the big-endian ushorts documented by BfresLibrary; the
/// decoder itself is this project's, not a copy of that library.
/// </summary>
public static class BfresReader
{
    const int F32 = 0x0516, F32_2 = 0x0517, F32_3 = 0x0518, F32_4 = 0x0519;
    const int F16 = 0x050A, F16_2 = 0x0512, F16_4 = 0x0515;
    const int U8 = 0x0102, U8_2 = 0x0109, U8_4 = 0x010B, S8_4 = 0x020B;
    const int UInt8 = 0x0302, UInt8_2 = 0x0309, UInt8_4 = 0x030B;
    const int U16_2 = 0x0112, S16_2 = 0x0212, SN10 = 0x020E, UN10 = 0x000B;
    static readonly Regex TexName = new(@"^[A-Za-z0-9_./+-]{1,120}$", RegexOptions.Compiled);

    public static List<ImportedModel> Read(byte[] data)
    {
        var file = new FileCursor(data);
        return file.Models();
    }

    /// <summary>
    /// Palette slot for <c>_i0</c>. Byte formats use the raw byte, not a 0–1 colour.
    /// Dividing a U8_4 byte by 255 and multiplying back can land on the next bone.
    /// </summary>
    public static int PaletteSlot(ReadOnlySpan<byte> buffer, int format, int at)
    {
        if ((uint)at >= (uint)buffer.Length)
            throw new ImportException("a bone index does not fit in its buffer");
        if (format is U8 or U8_2 or U8_4 or S8_4 or UInt8 or UInt8_2 or UInt8_4) return buffer[at];
        if (format == U16_2)
        {
            if (at + 2 > buffer.Length) throw new ImportException("a bone index does not fit in its buffer");
            return Bin.U16(buffer, at);
        }
        return -1;
    }

    sealed class FileCursor
    {
        readonly byte[] _data;
        public readonly int Major;
        readonly long _bufferBase;
        readonly int _extValues;
        readonly int _extDict;
        readonly int _modelValues;
        readonly int _modelDict;

        public FileCursor(byte[] data)
        {
            if (data.Length < 0x100 || data[0] != (byte)'F' || data[1] != (byte)'R' || data[2] != (byte)'E' || data[3] != (byte)'S')
                throw new ImportException("not a BFRES file");
            if (data[4] != (byte)' ' || data[5] != (byte)' ' || data[6] != (byte)' ' || data[7] != (byte)' ')
                throw new ImportException("this is not a Switch BFRES (expected four spaces after FRES)");
            if (data[0x0C] != 0xFF || data[0x0D] != 0xFE)
                throw new ImportException("only little-endian BFRES is supported");
            _data = data;
            int version = (int)Bin.U32(data, 0x08);
            Major = (version >> 16) & 0xFFFF;
            int fileSize = (int)Bin.U32(data, 0x1C);
            if (Major is < 4 or > 12)
                throw new ImportException($"BFRES version {Major} is not one this viewer reads");
            int anim = Major >= 9 ? 0x58 : 0x38;
            int info = (int)Bin.U64(data, anim + 88);
            long buffer = info != 0 ? Bin.I64(data, info + 8) : 0;
            _extValues = (int)Bin.U64(data, anim + 96);
            _extDict = (int)Bin.U64(data, anim + 104);
            _modelValues = (int)Bin.U64(data, 0x28);
            _modelDict = (int)Bin.U64(data, 0x30);
            int flagAt = Major >= 9 ? 0xEE : 0xCA;
            int flag = data.Length > flagAt ? data[flagAt] : 0;
            if ((flag & 0x08) != 0) buffer = fileSize + 288L;
            if (buffer != 0 && buffer >= data.Length)
                throw new ImportException(
                    $"the vertex buffer is an external GPU section past the end of this file (flag 0x{flag:X2}, buffer at 0x{buffer:X})");
            _bufferBase = buffer;
        }

        public List<ImportedModel> Models()
        {
            var keys = DictKeys(_modelDict);
            if (keys.Count == 0 || _modelValues == 0)
                throw new ImportException("the BFRES has no models");
            var images = Images(out var imageNotes);
            List<ImportedClip> clips;
            try { clips = FskaReader.Read(_data, Major); }
            catch (ImportException) { clips = new List<ImportedClip>(); }
            var found = new List<ImportedModel>();
            int at = _modelValues;
            foreach (string key in keys)
            {
                var (model, next) = Model(at, key, images);
                model.Notes.AddRange(imageNotes);
                model.Clips = clips;
                SkinBinder.Apply(model);
                found.Add(model);
                at = next;
            }
            return found;
        }

        List<string> DictKeys(int at)
        {
            if (at == 0) return new List<string>();
            int count = Bin.I32(_data, at + 4);
            if (count is < 0 or > 100000)
                throw new ImportException("a name dictionary has a count this reader will not walk");
            var keys = new List<string>(count);
            for (int index = 1; index <= count; index++)
            {
                int node = at + 8 + index * 16;
                keys.Add(Bin.LengthString(_data, (int)Bin.U64(_data, node + 8)));
            }
            return keys;
        }

        Dictionary<string, CompressedTexture> Images(out List<string> notes)
        {
            notes = new List<string>();
            var images = new Dictionary<string, CompressedTexture>(StringComparer.OrdinalIgnoreCase);
            var keys = DictKeys(_extDict);
            int at = _extValues;
            foreach (string key in keys)
            {
                if (at == 0 || at + 16 > _data.Length) break;
                int offset = (int)Bin.U64(_data, at);
                int size = (int)Bin.U64(_data, at + 8);
                at += 16;
                if (offset <= 0 || size <= 0 || offset + size > _data.Length) continue;
                var blob = _data.AsSpan(offset, size).ToArray();
                if (!BntxReader.LooksLike(blob)) continue;
                try
                {
                    foreach (var image in BntxReader.Read(blob))
                        images[image.Name] = image;
                }
                catch (ImportException exc)
                {
                    notes.Add($"{(key.Length == 0 ? "embedded texture" : key)}: {exc.Message}");
                }
            }
            return images;
        }

        (ImportedModel Model, int End) Model(int at, string fallback, Dictionary<string, CompressedTexture> images)
        {
            if (at + 4 > _data.Length || _data[at] != (byte)'F' || _data[at + 1] != (byte)'M')
                throw new ImportException($"expected a model at 0x{at:X}");
            int cursor = at + 4 + (Major >= 9 ? 4 : 12);
            string name = Bin.LengthString(_data, (int)Bin.U64(_data, cursor));
            cursor += 16;
            int skel = (int)Bin.U64(_data, cursor);
            cursor += 16;
            int shapeValues = (int)Bin.U64(_data, cursor);
            cursor += 8;
            int shapeDict = (int)Bin.U64(_data, cursor);
            cursor += 8;
            int matValues, matDict;
            if (Major >= 10)
            {
                matValues = (int)Bin.U64(_data, cursor);
                cursor += 8;
                matDict = (int)Bin.U64(_data, cursor);
                cursor += 16;
            }
            else if (Major >= 9)
            {
                matValues = (int)Bin.U64(_data, cursor);
                cursor += 8;
                int maybe = (int)Bin.U64(_data, cursor);
                cursor += 8;
                matDict = (int)Bin.U64(_data, cursor);
                cursor += 8;
                if (matDict == 0) matDict = maybe;
            }
            else
            {
                matValues = (int)Bin.U64(_data, cursor);
                cursor += 8;
                matDict = (int)Bin.U64(_data, cursor);
                cursor += 8;
            }
            cursor += 24;
            int materialCount = Bin.U16(_data, cursor + 4);
            cursor += 6;
            cursor += 10;
            int end = cursor;
            List<BindPose.Bone> bones;
            List<ImportedBone> skeleton;
            List<int> palette;
            int smoothMatrices = 0, rigidMatrices = 0;
            if (skel != 0) (bones, skeleton) = Bones(skel, out palette, out smoothMatrices, out rigidMatrices);
            else
            {
                bones = new List<BindPose.Bone>();
                skeleton = new List<ImportedBone>();
                palette = new List<int>();
            }
            var materials = Materials(matValues, matDict, materialCount);
            var shapeNames = DictKeys(shapeDict);
            var parts = new List<ImportedPart>();
            var notes = new List<string>();
            int undecoded = 0;
            int shapeAt = shapeValues;
            for (int index = 0; index < shapeNames.Count; index++)
            {
                string shapeName = shapeNames[index].Length == 0 ? $"shape{index}" : shapeNames[index];
                try
                {
                    var (made, next, flagged) = Shape(shapeAt, shapeName, bones, palette, materials);
                    shapeAt = next;
                    if (made.Count > 0) parts.AddRange(made);
                    else if (flagged) undecoded++;
                }
                catch (ImportException exc)
                {
                    notes.Add($"{shapeName}: {exc.Message}");
                    break;
                }
            }
            var near = parts.Where(part => part.Lod == 0).ToList();
            var far = parts.Where(part => part.Lod != 0).ToList();
            parts = DropOpaqueTwins(near);
            parts.AddRange(far);
            if (undecoded > 0 && parts.Count == 0) notes.Add("geometry format not decoded (FSHP flag 0x10)");
            else if (undecoded > 0) notes.Add($"{undecoded} shapes use FSHP flag 0x10 and were not decoded");
            else if (parts.Count == 0) notes.Add($"{(name.Length == 0 ? fallback : name)}: no drawable shape");
            ShapeFilter.Hide(parts);
            return (new ImportedModel
            {
                Name = name.Length == 0 ? fallback : name,
                Parts = parts,
                Images = images,
                Skeleton = skeleton,
                MatrixPalette = palette,
                SmoothMatrices = smoothMatrices,
                RigidMatrices = rigidMatrices,
                Notes = notes,
            }, end);
        }

        (List<BindPose.Bone> Bones, List<ImportedBone> Skeleton) Bones(int at, out List<int> palette, out int smoothMatrices, out int rigidMatrices)
        {
            palette = new List<int>();
            smoothMatrices = 0;
            rigidMatrices = 0;
            var bones = new List<BindPose.Bone>();
            var skeleton = new List<ImportedBone>();
            if (at + 4 > _data.Length || _data[at] != (byte)'F' || _data[at + 1] != (byte)'S')
                return (bones, skeleton);
            int cursor = at + 4 + (Major >= 9 ? 4 : 12);
            int dict = (int)Bin.U64(_data, cursor);
            cursor += 8;
            int array = (int)Bin.U64(_data, cursor);
            var names = DictKeys(dict);
            cursor = array;
            foreach (string rawName in names)
            {
                if (cursor == 0) break;
                cursor += 24;
                if (Major > 9) cursor += 8;
                else if (Major is 8 or 9) cursor += 16;
                cursor += 2;
                int parent = Bin.I16(_data, cursor);
                cursor += 2;
                int smoothIndex = Bin.I16(_data, cursor);
                cursor += 2;
                int rigid = Bin.I16(_data, cursor);
                cursor += 2;
                cursor += 4;
                uint flags = Bin.U32(_data, cursor);
                cursor += 4;
                double sx = Bin.F32(_data, cursor), sy = Bin.F32(_data, cursor + 4), sz = Bin.F32(_data, cursor + 8);
                cursor += 12;
                double r0 = Bin.F32(_data, cursor), r1 = Bin.F32(_data, cursor + 4), r2 = Bin.F32(_data, cursor + 8), r3 = Bin.F32(_data, cursor + 12);
                cursor += 16;
                double px = Bin.F32(_data, cursor), py = Bin.F32(_data, cursor + 4), pz = Bin.F32(_data, cursor + 8);
                cursor += 12;
                double rx, ry, rz, rw;
                if ((flags & 0x1000) != 0)
                {
                    var quat = BindPose.Euler(r0, r1, r2);
                    rx = quat.X; ry = quat.Y; rz = quat.Z; rw = quat.W;
                }
                else { rx = r0; ry = r1; rz = r2; rw = r3; }
                bones.Add(new BindPose.Bone(parent, px, py, pz, rx, ry, rz, rw, sx, sy, sz, flags, rigid));
                string boneName = rawName.Length == 0 ? $"bone_{bones.Count - 1}" : rawName;
                skeleton.Add(new ImportedBone
                {
                    Name = boneName,
                    Parent = parent,
                    Px = px, Py = py, Pz = pz,
                    Rx = rx, Ry = ry, Rz = rz, Rw = rw,
                    Sx = sx, Sy = sy, Sz = sz,
                    ScaleCompensate = (flags & 0x800000) != 0,
                    Smooth = smoothIndex,
                    Rigid = rigid,
                    BindSx = sx, BindSy = sy, BindSz = sz,
                });
            }
            palette = MatrixPalette(at, bones.Count, out smoothMatrices, out rigidMatrices);
            return (bones, skeleton);
        }

        List<int> MatrixPalette(int at, int boneCount, out int smooth, out int rigid)
        {
            smooth = 0;
            rigid = 0;
            if (Major < 9 || boneCount <= 0 || at + 0x40 > _data.Length) return new List<int>();
            int paletteAt = (int)Bin.U64(_data, at + 0x18);
            smooth = Bin.U16(_data, at + 0x3A);
            rigid = Bin.U16(_data, at + 0x3C);
            int count = smooth + rigid;
            if (paletteAt == 0 || count is <= 0 or > 4096 || paletteAt + count * 2 > _data.Length) return new List<int>();
            var palette = new List<int>(count);
            for (int i = 0; i < count; i++)
            {
                int index = Bin.U16(_data, paletteAt + i * 2);
                if (index >= boneCount) return new List<int>();
                palette.Add(index);
            }
            return palette;
        }

        List<MaterialView> Materials(int values, int dictionary, int declared)
        {
            var names = DictKeys(dictionary);
            int stride = Major >= 10 ? 0xB0 : Major >= 9 ? 0xA8 : 0xB8;
            if (Major >= 9 && values != 0 && values + 4 <= _data.Length && _data[values] == (byte)'F' && _data[values + 1] == (byte)'M')
            {
                int count = names.Count;
                if (declared > count)
                {
                    int present = 0;
                    for (int index = 0; index < Math.Min(declared, 256); index++)
                    {
                        int slot = values + index * stride;
                        if (slot + 4 > _data.Length || _data[slot] != (byte)'F' || _data[slot + 1] != (byte)'M') break;
                        present++;
                    }
                    if (present > count) count = present;
                }
                if (count <= 0) count = 1;
                var found = new List<MaterialView>();
                for (int index = 0; index < count; index++)
                {
                    int slot = values + index * stride;
                    if (slot + stride > _data.Length || _data[slot] != (byte)'F') break;
                    found.Add(ReadMaterial(slot, index < names.Count ? names[index] : ""));
                }
                if (found.Count > 0) return found;
            }
            var walked = new List<MaterialView>();
            int at = values;
            foreach (string name in names)
            {
                if (at == 0 || at + 4 > _data.Length || _data[at] != (byte)'F')
                {
                    walked.Add(new MaterialView(name));
                    continue;
                }
                walked.Add(ReadMaterial(at, name));
                at += stride;
            }
            return walked;
        }

        MaterialView ReadMaterial(int at, string fallback)
        {
            if (Major >= 10) return ReadMaterialV10(at, fallback);
            int nameAt, textureAt, samplerDict, samplerCount, textureCount;
            if (Major >= 9)
            {
                if (at + 0xA8 > _data.Length) return new MaterialView(fallback);
                nameAt = (int)Bin.U64(_data, at + 0x08);
                textureAt = (int)Bin.U64(_data, at + 0x30);
                samplerDict = (int)Bin.U64(_data, at + 0x48);
                samplerCount = _data[at + 0x9C];
                textureCount = _data[at + 0x9D];
            }
            else
            {
                if (at + 0xB4 > _data.Length) return new MaterialView(fallback);
                nameAt = (int)Bin.U64(_data, at + 0x10);
                textureAt = (int)Bin.U64(_data, at + 0x38);
                samplerDict = (int)Bin.U64(_data, at + 0x50);
                samplerCount = _data[at + 0xA8];
                textureCount = _data[at + 0xA9];
            }
            string name = Bin.LengthString(_data, nameAt);
            if (name.Length == 0) name = fallback;
            var textures = NameList(textureAt, textureCount);
            if (textures.Count == 0 && samplerCount > 0) textures = NameList(textureAt, samplerCount);
            List<string> samplers;
            try { samplers = samplerDict != 0 ? DictKeys(samplerDict) : new List<string>(); }
            catch (ImportException) { samplers = new List<string>(); }
            var binds = new List<(string, string)>();
            int count = Math.Max(textures.Count, samplers.Count);
            for (int index = 0; index < count; index++)
            {
                string texture = index < textures.Count ? textures[index] : "";
                string sampler = index < samplers.Count ? samplers[index] : "";
                if (texture.Length == 0 && sampler.Length == 0) continue;
                binds.Add((sampler, texture));
            }
            return Finish(name, binds, new List<string>());
        }

        MaterialView ReadMaterialV10(int at, string fallback)
        {
            if (at + 0xB0 > _data.Length || _data[at] != (byte)'F') return new MaterialView(fallback);
            string name = Bin.LengthString(_data, (int)Bin.U64(_data, at + 0x08));
            if (name.Length == 0) name = fallback;
            int textureAt = (int)Bin.U64(_data, at + 0x20);
            int samplerDict = (int)Bin.U64(_data, at + 0x38);
            int stateAt = (int)Bin.U64(_data, at + 0x40);
            List<string> samplers;
            try { samplers = samplerDict != 0 ? DictKeys(samplerDict) : new List<string>(); }
            catch (ImportException) { samplers = new List<string>(); }
            var lists = new List<List<string>>();
            foreach (int count in new[] { _data[at + 0xA2], _data[at + 0xA3], samplers.Count })
            {
                var names = NameList(textureAt, count);
                if (names.Count > 0) lists.Add(names);
            }
            if (lists.Count == 0 && textureAt != 0 && samplers.Count == 0 && _data[at + 0xA2] == 0 && _data[at + 0xA3] == 0)
            {
                var walked = NameList(textureAt, 16);
                if (walked.Count > 0) lists.Add(walked);
            }
            var textures = lists.Count == 0 ? new List<string>() : lists.OrderByDescending(item => item.Count).First();
            var states = StateStrings(stateAt, 31);
            var binds = new List<(string, string)>();
            int pairCount = Math.Min(64, Math.Max(textures.Count, samplers.Count));
            for (int index = 0; index < pairCount; index++)
            {
                string texture = index < textures.Count ? textures[index] : "";
                string sampler = index < samplers.Count ? samplers[index] : "";
                if (texture.Length == 0 && sampler.Length == 0) continue;
                binds.Add((sampler, texture));
            }
            return Finish(name, binds, states);
        }

        MaterialView Finish(string name, List<(string, string)> binds, List<string> states)
        {
            var ranked = AlbedoRank.Rank(binds);
            string alpha = "opaque";
            bool sided = true;
            if (states.Count > 11)
            {
                string cull = Slot(states, 10);
                string mode = Slot(states, 11);
                string test = Slot(states, 15);
                if (cull.Length > 0)
                {
                    if (cull.Contains("both") || cull.Contains("double") || cull.Contains("none") || cull.Contains("frontandback"))
                        sided = true;
                    else if (cull.Contains("front") || cull.Contains("back"))
                        sided = false;
                }
                if (mode.Contains("translucen") || mode == "blend") alpha = "blend";
                else if (mode.Contains("mask") || mode.Contains("clip") || mode.Contains("cutout") || test is "true" or "1" or "yes")
                    alpha = "mask";
            }
            return new MaterialView(name, binds, ranked, alpha, sided);
        }

        static string Slot(List<string> states, int index) =>
            index >= states.Count ? "" : states[index].Trim().ToLowerInvariant();

        List<string> StateStrings(int at, int count)
        {
            if (at == 0 || count is <= 0 or > 64) return new List<string>();
            var found = new List<string>();
            for (int index = 0; index < count; index++)
            {
                int slot = at + index * 8;
                if (slot + 8 > _data.Length) break;
                int ptr = (int)Bin.U64(_data, slot);
                if (ptr == 0 || !IsString(ptr)) break;
                found.Add(Bin.LengthString(_data, ptr));
            }
            return found;
        }

        bool IsString(int at)
        {
            if (at <= 0 || at + 3 > _data.Length) return false;
            int length = Bin.U16(_data, at);
            if (length > 160) return false;
            int end = at + 2 + length;
            if (end >= _data.Length || _data[end] != 0) return false;
            for (int i = at + 2; i < end; i++)
                if (_data[i] > 0x7F) return false;
            return true;
        }

        List<string> NameList(int at, int count)
        {
            if (at == 0 || count is <= 0 or > 64) return new List<string>();
            var names = new List<string>();
            for (int index = 0; index < count; index++)
            {
                int slot = at + index * 8;
                if (slot + 8 > _data.Length) break;
                string text = Bin.LengthString(_data, (int)Bin.U64(_data, slot));
                if (text.Length == 0 || !TexName.IsMatch(text))
                {
                    if (index == 0) return new List<string>();
                    break;
                }
                names.Add(text);
            }
            return names;
        }

        (List<ImportedPart> Parts, int End, bool Flagged) Shape(
            int at, string name, List<BindPose.Bone> bones, List<int> palette, List<MaterialView> materials)
        {
            if (at + 4 > _data.Length || _data[at] != (byte)'F' || _data[at + 1] != (byte)'S')
                throw new ImportException($"expected a shape at 0x{at:X}");
            uint flags = Major >= 9 ? Bin.U32(_data, at + 4) : 0;
            int cursor = at + 4 + (Major >= 9 ? 4 : 12);
            cursor += 8;
            int vtx = (int)Bin.U64(_data, cursor);
            cursor += 8;
            int meshAt = (int)Bin.U64(_data, cursor);
            cursor += 8 + 8 + 16 + 8 + 8 + 8;
            if (Major < 9) cursor += 4;
            cursor += 2;
            int materialIndex = Bin.U16(_data, cursor);
            cursor += 2;
            int boneIndex = Bin.U16(_data, cursor);
            cursor += 2;
            cursor += 2;
            int skinBoneCount = Bin.U16(_data, cursor);
            cursor += 2;
            int skinCount = _data[cursor];
            cursor += 1;
            int meshCount = _data[cursor];
            cursor += 1;
            cursor += 1;
            cursor += 1;
            cursor += Major <= 3 || Major >= 9 ? 2 : 6;
            int end = cursor;
            bool standard = vtx != 0 && meshCount > 0 && vtx + 4 <= _data.Length
                && _data[vtx] == (byte)'F' && _data[vtx + 1] == (byte)'V';
            var material = materialIndex >= 0 && materialIndex < materials.Count ? materials[materialIndex] : null;
            var shapeBones = ShapeBones(at, skinBoneCount);
            if (!standard)
            {
                if ((flags & 0x10) != 0 && Bin.U32(_data, 0x08) == 0x000A0400)
                {
                    if (skinCount != 0)
                        return (new List<ImportedPart>(), end, true);
                    try
                    {
                        var levels = MeshletDecoder.Decode(_data, at, (int)_bufferBase);
                        var made = new List<ImportedPart>();
                        foreach (var level in levels)
                        {
                            var positions = level.Positions.Select(item => new Vec3(item.X, item.Y, item.Z)).ToList();
                            List<VertexSkin>? weights = null;
                            if (bones.Count > 0 && skinCount == 0)
                                weights = RigidSkin(boneIndex, bones.Count, positions.Count);
                            var uvs = level.UvSets.Count == 0 ? null : level.UvSets[0].Select(item => new Vec2(item.U, item.V)).ToList();
                            var normals = level.Normals.Count == 0 ? null : level.Normals.Select(item => new Vec3(item.X, item.Y, item.Z)).ToList();
                            var meshlet = PartFrom(name, positions, level.Indices, uvs, normals, material, materialIndex, level.Number, weights);
                            meshlet.SkinCount = skinCount;
                            meshlet.ShapeBones = shapeBones;
                            made.Add(meshlet);
                        }
                        return (made, end, false);
                    }
                    catch (ImportException)
                    {
                        return (new List<ImportedPart>(), end, true);
                    }
                }
                return (new List<ImportedPart>(), end, (flags & 0x10) != 0);
            }
            var fvtx = Fvtx(vtx);
            var lod = Lod(meshAt);
            if (fvtx is null || lod is null) return (new List<ImportedPart>(), end, (flags & 0x10) != 0);
            var (positions2, uvs2) = Vertices(fvtx, lod);
            if (positions2.Count == 0 || lod.Indices.Count == 0)
                return (new List<ImportedPart>(), end, (flags & 0x10) != 0);
            var normals2 = ReadNormals(fvtx, positions2.Count);
            var weights2 = SkinOf(fvtx, positions2.Count, skinCount, boneIndex, bones);
            var made2 = new List<ImportedPart>
            {
                Tag(PartFrom(name, positions2, lod.Indices, uvs2, normals2, material, materialIndex, 0, weights2), skinCount, shapeBones),
            };
            if (meshCount > 1)
            {
                (List<Vec3> Pos, List<Vec2>? Uv, List<Vec3>? Normal, List<VertexSkin>? Weights, List<int> Idx)? best = null;
                int bestLevel = 0;
                for (int level = 1; level < Math.Min(meshCount, 4); level++)
                {
                    LodMesh? coarser;
                    try { coarser = Lod(meshAt + level * 0x38); }
                    catch (ImportException) { break; }
                    if (coarser is null || coarser.Indices.Count == 0 || coarser.Indices.Count >= lod.Indices.Count) continue;
                    try
                    {
                        var (farPos, farUv) = Vertices(fvtx, coarser);
                        var farNormals = ReadNormals(fvtx, farPos.Count);
                        var farWeights = SkinOf(fvtx, farPos.Count, skinCount, boneIndex, bones);
                        if (farPos.Count == 0) continue;
                        best = (farPos, farUv, farNormals, farWeights, coarser.Indices);
                        bestLevel = level;
                    }
                    catch (ImportException) { continue; }
                }
                if (best is not null)
                    made2.Add(Tag(PartFrom(name, best.Value.Pos, best.Value.Idx, best.Value.Uv, best.Value.Normal, material, materialIndex, bestLevel, best.Value.Weights), skinCount, shapeBones));
            }
            return (made2, end, false);
        }

        void Bake(List<Vec3> positions, List<BindPose.Bone> bones, List<int> palette, int boneIndex, List<int>? slots)
        {
            var raw = positions.Select(item => (item.X, item.Y, item.Z)).ToList();
            BindPose.Bake(raw, bones, palette, boneIndex, slots);
            for (int i = 0; i < positions.Count; i++)
                positions[i] = new Vec3(raw[i].X, raw[i].Y, raw[i].Z);
        }

        FvtxBuffer Fvtx(int at)
        {
            if (at + 0x40 > _data.Length || _data[at] != (byte)'F' || _data[at + 1] != (byte)'V')
                throw new ImportException($"expected a vertex buffer at 0x{at:X}");
            int cursor = at + 4 + (Major >= 9 ? 4 : 12);
            int attrAt = (int)Bin.U64(_data, cursor);
            cursor += 16 + 8 + 8;
            if (Major > 2) cursor += 8;
            int sizeAt = (int)Bin.U64(_data, cursor);
            cursor += 8;
            int strideAt = (int)Bin.U64(_data, cursor);
            cursor += 16;
            int bufferOff = Bin.I32(_data, cursor);
            cursor += 4;
            int numAttr = _data[cursor];
            cursor += 1;
            int numBuf = _data[cursor];
            cursor += 3;
            int count = (int)Bin.U32(_data, cursor);
            cursor += 6;
            int align = Major >= 10 ? Bin.U16(_data, cursor) : 1;
            if (align <= 0) align = 1;
            if (numBuf is <= 0 or > 16 || count > 5_000_000)
                throw new ImportException("vertex buffer counts are not usable");
            var attributes = new List<Attribute>(numAttr);
            int pos = attrAt;
            for (int i = 0; i < numAttr; i++)
            {
                attributes.Add(new Attribute(
                    Bin.LengthString(_data, (int)Bin.U64(_data, pos)),
                    BinaryPrimitives(pos + 8),
                    Bin.U16(_data, pos + 12),
                    Bin.U16(_data, pos + 14)));
                pos += 16;
            }
            var strides = new int[numBuf];
            var sizes = new int[numBuf];
            for (int i = 0; i < numBuf; i++)
            {
                strides[i] = (int)Bin.U32(_data, strideAt + i * 16);
                sizes[i] = (int)Bin.U32(_data, sizeAt + i * 16);
            }
            var buffers = new byte[numBuf][];
            long dataAt = _bufferBase + bufferOff;
            for (int i = 0; i < numBuf; i++)
            {
                dataAt = (dataAt + align - 1) & ~(align - 1L);
                int size = sizes[i];
                if (dataAt < 0 || size < 0 || dataAt + size > _data.Length)
                    throw new ImportException(
                        "a vertex buffer does not fit in this file. The mesh may be stored in a GPU section or a .bfres.mc stream this viewer does not read.");
                buffers[i] = _data.AsSpan((int)dataAt, size).ToArray();
                dataAt += size;
            }
            return new FvtxBuffer(count, attributes, strides, buffers);
        }

        int BinaryPrimitives(int at) => (_data[at] << 8) | _data[at + 1];

        LodMesh? Lod(int at)
        {
            if (at == 0) return null;
            int subAt = (int)Bin.U64(_data, at);
            int sizeAt = (int)Bin.U64(_data, at + 24);
            uint faceOff = Bin.U32(_data, at + 32);
            int primitive = (int)Bin.U32(_data, at + 36);
            int indexFormat = (int)Bin.U32(_data, at + 40);
            int indexCount = (int)Bin.U32(_data, at + 44);
            int first = (int)Bin.U32(_data, at + 48);
            int subCount = Bin.U16(_data, at + 52);
            int size = sizeAt != 0 ? (int)Bin.U32(_data, sizeAt) : 0;
            long start = _bufferBase + faceOff;
            if (start < 0 || size < 0 || start + size > _data.Length)
                throw new ImportException("the index buffer does not fit in this file");
            var blob = _data.AsSpan((int)start, size);
            int width = indexFormat switch { 0 => 1, 1 => 2, 2 => 4, _ => 0 };
            if (width == 0) throw new ImportException($"index format {indexFormat} is not decoded");
            var ranges = new List<(int Offset, int Count)>();
            if (subCount > 0 && subAt != 0)
            {
                for (int i = 0; i < subCount; i++)
                    ranges.Add(((int)Bin.U32(_data, subAt + i * 8), (int)Bin.U32(_data, subAt + i * 8 + 4)));
            }
            else ranges.Add((0, indexCount));
            var indices = new List<int>();
            foreach (var (offset, count) in ranges)
            {
                for (int item = 0; item < count; item++)
                {
                    int pos = offset + item * width;
                    if (pos + width > blob.Length) break;
                    int value = width switch
                    {
                        1 => blob[pos],
                        2 => Bin.U16(blob, pos),
                        _ => (int)Bin.U32(blob, pos),
                    };
                    indices.Add(value + first);
                }
            }
            return new LodMesh(Triangles(indices, primitive));
        }

        static List<int> Triangles(List<int> indices, int primitive)
        {
            if (primitive == 4)
            {
                var output = new List<int>();
                for (int index = 0; index < indices.Count - 2; index++)
                {
                    int a = indices[index], b = indices[index + 1], c = indices[index + 2];
                    if (a == b || b == c || a == c) continue;
                    if ((index & 1) == 0) { output.Add(a); output.Add(b); output.Add(c); }
                    else { output.Add(a); output.Add(c); output.Add(b); }
                }
                return output;
            }
            if (primitive != 3) throw new ImportException($"primitive type {primitive} is not triangles");
            int keep = indices.Count - (indices.Count % 3);
            return indices.GetRange(0, keep);
        }

        (List<Vec3> Positions, List<Vec2>? Uvs) Vertices(FvtxBuffer fvtx, LodMesh lod)
        {
            var position = Attr(fvtx, "_p0", "p0", "_p");
            var uv = Attr(fvtx, "_u0", "u0", "_u");
            if (position is null) throw new ImportException("the shape has no position attribute (_p0)");
            var positions = new List<Vec3>(fvtx.Count);
            List<Vec2>? uvs = uv is null ? null : new List<Vec2>(fvtx.Count);
            for (int index = 0; index < fvtx.Count; index++)
            {
                var raw = Decode(fvtx, position, index);
                positions.Add(new Vec3(raw[0], raw.Length > 1 ? raw[1] : 0, raw.Length > 2 ? raw[2] : 0));
                if (uv is not null && uvs is not null)
                {
                    var tex = Decode(fvtx, uv, index);
                    uvs.Add(new Vec2(tex[0], tex.Length > 1 ? tex[1] : 0));
                }
            }
            if (lod.Indices.Count > 0 && lod.Indices.Max() >= fvtx.Count)
                throw new ImportException("an index points past the vertex buffer");
            return (positions, uvs);
        }

        List<int>? SkinSlots(FvtxBuffer fvtx, int count)
        {
            var attribute = Attr(fvtx, "_i0", "i0", "_i");
            if (attribute is null) return null;
            var slots = new List<int>(count);
            try
            {
                for (int index = 0; index < count; index++)
                    slots.Add(ReadIndex(fvtx, attribute, index));
            }
            catch (ImportException) { return null; }
            return slots;
        }

        static Attribute? Attr(FvtxBuffer fvtx, params string[] names)
        {
            foreach (string name in names)
            {
                var found = fvtx.Attributes.FirstOrDefault(item => item.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
                if (found is not null) return found;
            }
            return null;
        }

        static int ReadIndex(FvtxBuffer fvtx, Attribute attribute, int index)
        {
            var buffer = fvtx.Buffers[attribute.Buffer];
            int stride = fvtx.Strides[attribute.Buffer];
            if (stride == 0) stride = FormatSize(attribute.Format);
            int at = index * stride + attribute.Offset;
            int raw = PaletteSlot(buffer, attribute.Format, at);
            if (raw >= 0) return raw;
            return (int)Math.Round(Decode(fvtx, attribute, index)[0] * 255.0);
        }

        List<Vec3>? ReadNormals(FvtxBuffer fvtx, int count)
        {
            var attribute = Attr(fvtx, "_n0", "n0", "_n");
            if (attribute is null) return null;
            var normals = new List<Vec3>(count);
            try
            {
                for (int index = 0; index < count; index++)
                {
                    var raw = Decode(fvtx, attribute, index);
                    normals.Add(new Vec3(raw[0], raw.Length > 1 ? raw[1] : 0, raw.Length > 2 ? raw[2] : 0));
                }
            }
            catch (ImportException)
            {
                return null;
            }
            return normals;
        }

        static double[] Decode(FvtxBuffer fvtx, Attribute attribute, int index)
        {
            if (attribute.Buffer >= fvtx.Buffers.Length)
                throw new ImportException($"attribute {attribute.Name} names a missing buffer");
            var blob = fvtx.Buffers[attribute.Buffer];
            int stride = fvtx.Strides[attribute.Buffer];
            if (stride == 0) stride = FormatSize(attribute.Format);
            int at = index * stride + attribute.Offset;
            int size = FormatSize(attribute.Format);
            if (size <= 0 || at + size > blob.Length)
                throw new ImportException($"vertex format 0x{attribute.Format:X4} does not fit in its buffer");
            return attribute.Format switch
            {
                F32 => new[] { (double)Bin.F32(blob, at) },
                F32_2 => new[] { (double)Bin.F32(blob, at), Bin.F32(blob, at + 4) },
                F32_3 => new[] { (double)Bin.F32(blob, at), Bin.F32(blob, at + 4), Bin.F32(blob, at + 8) },
                F32_4 => new[] { (double)Bin.F32(blob, at), Bin.F32(blob, at + 4), Bin.F32(blob, at + 8), Bin.F32(blob, at + 12) },
                F16 => new[] { (double)Bin.F16(blob, at) },
                F16_2 => new[] { (double)Bin.F16(blob, at), Bin.F16(blob, at + 2) },
                F16_4 => new[] { (double)Bin.F16(blob, at), Bin.F16(blob, at + 2), Bin.F16(blob, at + 4), Bin.F16(blob, at + 6) },
                U8 => new[] { blob[at] / 255.0 },
                U8_2 => new[] { blob[at] / 255.0, blob[at + 1] / 255.0 },
                U8_4 => new[] { blob[at] / 255.0, blob[at + 1] / 255.0, blob[at + 2] / 255.0, blob[at + 3] / 255.0 },
                S8_4 => new[] { S8(blob[at]) / 127.0, S8(blob[at + 1]) / 127.0, S8(blob[at + 2]) / 127.0, S8(blob[at + 3]) / 127.0 },
                U16_2 => new[] { Bin.U16(blob, at) / 65535.0, Bin.U16(blob, at + 2) / 65535.0 },
                S16_2 => new[] { Bin.I16(blob, at) / 32767.0, Bin.I16(blob, at + 2) / 32767.0 },
                SN10 or UN10 => Unpack10(Bin.U32(blob, at), attribute.Format == SN10),
                _ => throw new ImportException($"vertex format 0x{attribute.Format:X4} is not decoded"),
            };
        }

        static int S8(byte value) => value > 127 ? value - 256 : value;

        static double[] Unpack10(uint packed, bool signed)
        {
            var parts = new double[3];
            for (int shift = 0; shift < 30; shift += 10)
            {
                int raw = (int)((packed >> shift) & 0x3FF);
                if (signed && (raw & 0x200) != 0) raw -= 0x400;
                parts[shift / 10] = raw / (signed ? 511.0 : 1023.0);
            }
            return parts;
        }

        static int FormatSize(int fmt) => fmt switch
        {
            U8 or UInt8 => 1,
            U8_2 or UInt8_2 or F16 => 2,
            U8_4 or S8_4 or UInt8_4 or U16_2 or S16_2 or F16_2 or F32 or SN10 or UN10 => 4,
            F16_4 or F32_2 => 8,
            F32_3 => 12,
            F32_4 => 16,
            _ => 0,
        };

        static ImportedPart Tag(ImportedPart part, int skinCount, List<int> shapeBones)
        {
            part.SkinCount = skinCount;
            part.ShapeBones = shapeBones;
            return part;
        }

        List<int> ShapeBones(int at, int count)
        {
            var bones = new List<int>();
            if (Major < 9 || count is <= 0 or > 4096 || at + 0x28 > _data.Length) return bones;
            int listAt = (int)Bin.U64(_data, at + 0x20);
            if (listAt <= 0 || listAt + count * 2 > _data.Length) return bones;
            for (int i = 0; i < count; i++) bones.Add(Bin.U16(_data, listAt + i * 2));
            return bones;
        }

        List<VertexSkin>? SkinOf(FvtxBuffer fvtx, int count, int skinCount, int boneIndex, List<BindPose.Bone> bones)
        {
            if (bones.Count == 0 || count <= 0 || skinCount > 8) return null;
            if (skinCount == 0)
                return RigidSkin(boneIndex, bones.Count, count);
            var indexAttr = Attr(fvtx, "_i0", "i0", "_i");
            if (indexAttr is null) return skinCount == 1 ? null : RigidSkin(boneIndex, bones.Count, count);
            var weightAttr = Attr(fvtx, "_w0", "w0", "_w");
            var extraIndices = skinCount > 4 ? Attr(fvtx, "_i1", "i1") : null;
            var extraWeights = skinCount > 4 ? Attr(fvtx, "_w1", "w1") : null;
            if (skinCount > 4 && (weightAttr is null || extraIndices is null || extraWeights is null)) return null;
            var skin = new List<VertexSkin>(count);
            Span<int> ids = stackalloc int[8];
            Span<float> weights = stackalloc float[8];
            try
            {
                for (int vertex = 0; vertex < count; vertex++)
                {
                    ids.Clear();
                    weights.Clear();
                    ReadIndices(fvtx, indexAttr, vertex, ids[..4]);
                    weights[0] = 1;
                    if (skinCount > 1 && weightAttr is not null)
                    {
                        CopyWeights(Decode(fvtx, weightAttr, vertex), weights[..Math.Min(skinCount, 4)]);
                        if (extraIndices is not null && extraWeights is not null)
                        {
                            ReadIndices(fvtx, extraIndices, vertex, ids[4..]);
                            CopyWeights(Decode(fvtx, extraWeights, vertex), weights[4..skinCount]);
                        }
                        float total = 0;
                        for (int i = 0; i < skinCount; i++) total += weights[i];
                        if (total > 1e-6f)
                            for (int i = 0; i < skinCount; i++) weights[i] /= total;
                        else weights[0] = 1;
                    }
                    for (int i = 0; i < skinCount; i++)
                        if (weights[i] <= 0) ids[i] = 0;
                    skin.Add(new VertexSkin(ids[0], ids[1], ids[2], ids[3], weights[0], weights[1], weights[2], weights[3],
                        ids[4], ids[5], ids[6], ids[7], weights[4], weights[5], weights[6], weights[7]));
                }
            }
            catch (ImportException) { return null; }
            return skin;
        }

        static List<VertexSkin>? RigidSkin(int boneIndex, int boneCount, int count)
        {
            if (boneIndex < 0 || boneIndex >= boneCount || count <= 0) return null;
            var skin = new List<VertexSkin>(count);
            var one = new VertexSkin(boneIndex, 0, 0, 0, 1, 0, 0, 0);
            for (int i = 0; i < count; i++) skin.Add(one);
            return skin;
        }

        static void CopyWeights(double[] values, Span<float> destination)
        {
            destination.Clear();
            for (int i = 0; i < Math.Min(values.Length, destination.Length); i++)
                destination[i] = (float)Math.Max(0, values[i]);
        }

        static void ReadIndices(FvtxBuffer fvtx, Attribute attribute, int index, Span<int> ids)
        {
            var buffer = fvtx.Buffers[attribute.Buffer];
            int stride = fvtx.Strides[attribute.Buffer];
            if (stride == 0) stride = FormatSize(attribute.Format);
            int at = index * stride + attribute.Offset;
            int byteCount = attribute.Format switch
            {
                U8_4 or S8_4 or UInt8_4 => 4,
                U8_2 or UInt8_2 => 2,
                _ => 0,
            };
            if (byteCount > 0)
            {
                if (at + byteCount > buffer.Length) throw new ImportException("a bone index does not fit in its buffer");
                for (int i = 0; i < byteCount; i++) ids[i] = buffer[at + i];
            }
            else ids[0] = ReadIndex(fvtx, attribute, index);
        }

        static ImportedPart PartFrom(
            string name, List<Vec3> positions, List<int> indices, List<Vec2>? uvs, List<Vec3>? normals,
            MaterialView? material, int materialIndex, int lod, List<VertexSkin>? weights = null)
        {
            string sampler = "", texture = "", overlay = "";
            if (material is not null && material.Albedo.Count > 0)
            {
                sampler = material.Albedo[0].Sampler;
                texture = material.Albedo[0].Texture;
                if (material.Albedo.Count > 1) overlay = material.Albedo[1].Texture;
            }
            string materialName = material?.Name ?? "";
            string omit = ShapeFilter.OmitReason(name, materialName);
            normals ??= positions.Count > 0 ? MeshNormals.Generate(positions, indices) : null;
            return new ImportedPart
            {
                Name = name,
                Lod = lod,
                Positions = positions,
                Indices = indices,
                Uvs = uvs,
                Normals = normals,
                Material = materialName,
                MaterialIndex = material is not null || materialIndex > 0 ? materialIndex : -1,
                Texture = texture,
                OverlayTexture = overlay,
                AlbedoSampler = sampler,
                Alpha = material?.Alpha ?? "opaque",
                DoubleSided = material?.DoubleSided ?? true,
                Binds = material?.Binds.ToList() ?? new List<(string, string)>(),
                OmitReason = omit,
                Helper = ShapeFilter.HelperKind(name, materialName),
                Water = ShapeFilter.IsWater(name, materialName),
                Weights = weights,
            };
        }

        static List<ImportedPart> DropOpaqueTwins(List<ImportedPart> parts)
        {
            var masked = new HashSet<string>(parts.Where(part => part.Alpha == "mask").Select(Key));
            if (masked.Count == 0) return parts;
            var kept = parts.Where(part => !(part.Alpha == "opaque" && masked.Contains(Key(part)))).ToList();
            return kept.Count == 0 ? parts : kept;
        }

        static string Key(ImportedPart part)
        {
            var points = string.Join(";", part.Positions.Select(p => $"{p.X:0.0000},{p.Y:0.0000},{p.Z:0.0000}"));
            var tris = new List<string>();
            for (int i = 0; i + 2 < part.Indices.Count; i += 3)
            {
                var ids = new[] { part.Indices[i], part.Indices[i + 1], part.Indices[i + 2] };
                Array.Sort(ids);
                tris.Add(string.Join(",", ids));
            }
            tris.Sort(StringComparer.Ordinal);
            return points + "|" + string.Join(";", tris);
        }
    }

    sealed record Attribute(string Name, int Format, int Offset, int Buffer);
    sealed record FvtxBuffer(int Count, List<Attribute> Attributes, int[] Strides, byte[][] Buffers);
    sealed record LodMesh(List<int> Indices);

    sealed class MaterialView
    {
        public string Name { get; }
        public List<(string Sampler, string Texture)> Binds { get; }
        public List<(string Sampler, string Texture)> Albedo { get; }
        public string Alpha { get; }
        public bool DoubleSided { get; }

        public MaterialView(string name)
            : this(name, new List<(string, string)>(), new List<(string, string)>(), "opaque", true) { }

        public MaterialView(string name, List<(string, string)> binds, List<(string, string)> albedo, string alpha, bool sided)
        {
            Name = name;
            Binds = binds;
            Albedo = albedo;
            Alpha = alpha;
            DoubleSided = sided;
        }
    }
}
