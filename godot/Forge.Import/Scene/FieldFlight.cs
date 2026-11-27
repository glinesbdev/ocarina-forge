namespace OcarinaForge.Import;

public readonly record struct FlightPose(double X, double Y, double Z, double LookX, double LookY, double LookZ, bool Done);

/// <summary>The browser <c>field-loop-160m</c> path. Steps are a fixed 1/60 s, not the real frame delta.</summary>
public sealed class FieldFlight
{
    public const string Scene = "hyrule-field-child";
    public const string PathName = "field-loop-160m";
    public const double EyeY = 190;
    public const double LookY = 150;
    public const double LookAhead = 48;
    public const double MetersPerSecond = 90;
    public const double FrameDt = 1.0 / 60.0;

    public static readonly (double X, double Z)[] Points =
    {
        (0, 0), (160, 0), (160, 160), (0, 160), (-160, 160),
        (-160, 0), (-160, -160), (0, -160), (160, -160), (0, 0),
    };

    int _segment;
    double _along;
    public bool Done { get; private set; }

    public FlightPose Sample(double dt)
    {
        var last = Points[^1];
        if (Done || Points.Length < 2)
        {
            Done = true;
            var prev = Points[Math.Max(0, Points.Length - 2)];
            return Pose(last.X, last.Z, last.X - prev.X, last.Z - prev.Z, Hypot(last.X - prev.X, last.Z - prev.Z));
        }
        double remain = Math.Max(0, MetersPerSecond * Math.Max(0, dt));
        while (remain > 0 && _segment < Points.Length - 1)
        {
            var a = Points[_segment];
            var b = Points[_segment + 1];
            double len = Hypot(b.X - a.X, b.Z - a.Z);
            if (len == 0) len = 1;
            double left = len - _along;
            if (remain < left) { _along += remain; remain = 0; }
            else { remain -= left; _segment++; _along = 0; }
        }
        if (_segment >= Points.Length - 1)
        {
            Done = true;
            _segment = Points.Length - 1;
            _along = 0;
        }
        var from = Points[Math.Min(_segment, Points.Length - 2)];
        var to = Points[Math.Min(_segment + 1, Points.Length - 1)];
        double span = Hypot(to.X - from.X, to.Z - from.Z);
        if (span == 0) span = 1;
        double t = Done ? 1 : _along / span;
        return Pose(from.X + (to.X - from.X) * t, from.Z + (to.Z - from.Z) * t, to.X - from.X, to.Z - from.Z, span);
    }

    public static int FramesUntilDone()
    {
        var flight = new FieldFlight();
        int frames = 0;
        while (!flight.Done && frames < 100000)
        {
            flight.Sample(FrameDt);
            frames++;
        }
        return frames;
    }

    static double Hypot(double x, double y) => Math.Sqrt(x * x + y * y);

    static FlightPose Pose(double x, double z, double dx, double dz, double len)
    {
        double scale = len > 0 ? LookAhead / len : 0;
        return new FlightPose(x, EyeY, z, x + dx * scale, LookY, z + dz * scale, false);
    }
}

public static class BenchmarkSummary
{
    public static readonly string[] Fields =
    {
        "submitMs", "updateMs", "priorityMs", "labelsMs", "cullMs",
        "writeTex", "indirectFills", "lodFlips", "heapBytes", "draws", "batches",
        "cpuMs", "gpuMs", "flyMs", "textureMs", "pipelines", "versions", "binds",
        "geomBytes", "bc7Bytes", "objects",
    };

    public static Dictionary<string, object?> Summarize(IReadOnlyList<Dictionary<string, double?>> samples)
    {
        var summary = new Dictionary<string, object?> { ["frames"] = samples.Count };
        foreach (string field in Fields)
        {
            var values = new List<double>();
            foreach (var sample in samples)
            {
                if (sample.TryGetValue(field, out var value) && value is double number && double.IsFinite(number))
                    values.Add(number);
            }
            summary[field] = new Dictionary<string, object?>
            {
                ["avg"] = values.Count == 0 ? null : values.Average(),
                ["p95"] = Percentile(values, 95),
                ["n"] = values.Count,
            };
        }
        return summary;
    }

    public static double? Percentile(List<double> values, double p)
    {
        if (values.Count == 0) return null;
        var sorted = values.OrderBy(item => item).ToList();
        int rank = (int)Math.Ceiling(p / 100.0 * sorted.Count) - 1;
        int index = Math.Clamp(rank, 0, sorted.Count - 1);
        return sorted[index];
    }
}
