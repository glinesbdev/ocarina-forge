namespace OcarinaForge.Import;

/// <summary>
/// Terrain blend shader. UV2.x is the chosen MaterialAlb layer, UV2.y is the other layer,
/// and COLOR.r is the blend toward the other layer. A layer below 0 is the flat earth colour.
/// </summary>
public static class TerrainShaderText
{
    public const string Code = """
        shader_type spatial;
        render_mode cull_disabled;
        uniform sampler2DArray albedo : source_color, repeat_enable, filter_linear_mipmap;
        uniform vec3 earth = vec3(0.45, 0.38, 0.22);
        varying flat int layer_a;
        varying flat int layer_b;
        varying float blend_w;
        void vertex() {
            layer_a = int(UV2.x);
            layer_b = int(UV2.y);
            blend_w = COLOR.r;
        }
        void fragment() {
            vec3 primary = layer_a < 0 ? earth : texture(albedo, vec3(UV, float(layer_a))).rgb;
            vec3 secondary = layer_b < 0 ? primary : texture(albedo, vec3(UV, float(layer_b))).rgb;
            ALBEDO = mix(primary, secondary, blend_w);
            ROUGHNESS = 1.0;
        }
        """;
}
