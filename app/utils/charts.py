"""Helpers de gráficos SVG (curva suave Catmull-Rom → Bézier)."""
from __future__ import annotations


def smooth_line_path(points: list[tuple[float, float]]) -> str:
    """Converte pontos em path SVG com curvas arredondadas."""
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M {x:.2f},{y:.2f}"

    # Espelha extremos para Catmull-Rom
    pts = [points[0], *points, points[-1]]
    d = [f"M {points[0][0]:.2f},{points[0][1]:.2f}"]
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        d.append(
            f"C {c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {p2[0]:.2f},{p2[1]:.2f}"
        )
    return " ".join(d)


def attach_chart_paths(
    chart: list[dict],
    *,
    value_key: str = "success",
    width: float = 700,
    max_h: float = 150,
    base_y: float = 195,
) -> tuple[list[dict], str, str, float]:
    """Adiciona x/y em cada ponto e devolve (chart, line_path, area_path, max_val)."""
    n = len(chart)
    step = width / (n - 1) if n > 1 else width
    max_val = max((float(pt.get(value_key) or 0) for pt in chart), default=0) or 1.0
    points: list[tuple[float, float]] = []
    for i, pt in enumerate(chart):
        x = i * step
        y = base_y - (float(pt.get(value_key) or 0) / max_val * max_h)
        pt["x"] = round(x, 2)
        pt["y"] = round(y, 2)
        points.append((x, y))

    line = smooth_line_path(points)
    if not points:
        return chart, "", "", max_val
    area = (
        f"{line} L {points[-1][0]:.2f},{base_y:.2f} "
        f"L {points[0][0]:.2f},{base_y:.2f} Z"
    )
    return chart, line, area, max_val
