"""Intervalos permitidos para automações recorrentes (minutos)."""

ALLOWED_INTERVALS = [
    10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60,
    90, 120, 180, 240, 360, 480, 720, 1080, 1440,
]


def interval_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutos"
    if minutes == 60:
        return "1 hora"
    if minutes < 1440:
        h = minutes / 60
        if h == int(h):
            return f"{int(h)} horas"
        return f"{minutes} minutos"
    return "24 horas"
