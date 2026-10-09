"""Feriados nacionais de Portugal e dias úteis."""
import datetime as dt


def pascoa(ano):
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19; b, c = divmod(ano, 100); d, e = divmod(b, 4)
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return dt.date(ano, mes, dia + 1)


def feriados(ano):
    p = pascoa(ano)
    fixos = [(1, 1), (4, 25), (5, 1), (6, 10), (8, 15), (10, 5), (11, 1), (12, 1), (12, 8), (12, 25)]
    return {dt.date(ano, m, d) for m, d in fixos} | {
        p - dt.timedelta(days=2),   # Sexta-feira Santa
        p,                          # Páscoa
        p + dt.timedelta(days=60),  # Corpo de Deus
    }


def dia_util(d):
    return d.weekday() < 5 and d not in feriados(d.year)


def dias_uteis_do_mes(ano, mes):
    d = dt.date(ano, mes, 1); out = []
    while d.month == mes:
        if dia_util(d): out.append(d)
        d += dt.timedelta(days=1)
    return out


def penultimo_dia_util(ano, mes):
    return dias_uteis_do_mes(ano, mes)[-2]
