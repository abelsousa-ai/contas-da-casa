"""Análises (R2).

Convenções:
- Custos: todas as categorias do tipo 'custo' (inclui Poupança e Investimentos), em valor líquido.
- Rendimento: valor líquido de 'Vencimentos & outros ganhos' (categoria do tipo 'rendimento').
- Movimentos sem Categoria entram em "Por classificar", incluída no total de custos.
- Movimentos previstos (por conciliar) contam logo que são registados.
- Percentagem de custos = custo da categoria ÷ total de custos do período.
"""
import datetime as dt
from collections import defaultdict

POR_CLASSIFICAR = 'Por classificar'


def _meta(con, chave, defeito=None):
    r = con.execute('SELECT valor FROM meta WHERE chave = ?', (chave,)).fetchone()
    return r[0] if r else defeito


def categorias(con):
    """Lista ordenada (nome, tipo) das categorias activas, mais 'Por classificar'."""
    cats = [(n, t) for n, t in con.execute('''SELECT nome, tipo FROM categorias WHERE activa = 1
                   OR id IN (SELECT DISTINCT categoria_id FROM movimentos) ORDER BY id''')]
    return cats + [(POR_CLASSIFICAR, 'custo')]


def _somas(con, de, ate):
    """Soma líquida por categoria entre duas datas (inclusive)."""
    out = defaultdict(float)
    for nome, total in con.execute('''
            SELECT COALESCE(c.nome, ?), SUM(m.valor) FROM movimentos m
            LEFT JOIN categorias c ON c.id = m.categoria_id
            WHERE m.data_lanc BETWEEN ? AND ? GROUP BY 1''', (POR_CLASSIFICAR, de, ate)):
        out[nome] = round(total, 2)
    return out


def _fim_mes(ano, mes):
    return (dt.date(ano + (mes == 12), mes % 12 + 1, 1) - dt.timedelta(days=1))


def mes_corrente(con, hoje: dt.date):
    """Vista b): consumo do mês até hoje, comparado com duas médias mensais por categoria.

    media_ano_anterior: total do ano anterior ÷ 12 (None se o ano anterior não está nos dados).
    media_ano_actual:   média dos meses completos do ano actual, sem o mês corrente (None em Janeiro).
    """
    inicio_dados = dt.date.fromisoformat(_meta(con, 'historico_de', '1900-01-01'))
    ano, mes = hoje.year, hoje.month
    consumo = _somas(con, dt.date(ano, mes, 1).isoformat(), hoje.isoformat())
    tem_ano_anterior = inicio_dados <= dt.date(ano - 1, 1, 1)
    ant = _somas(con, f'{ano - 1}-01-01', f'{ano - 1}-12-31') if tem_ano_anterior else None
    primeiro_mes = max(1, inicio_dados.month) if inicio_dados.year == ano else 1
    meses_completos = max(0, mes - primeiro_mes) if inicio_dados <= dt.date(ano, mes, 1) else 0
    act = (_somas(con, dt.date(ano, primeiro_mes, 1).isoformat(), _fim_mes(ano, mes - 1).isoformat())
           if meses_completos else None)
    linhas = []
    for nome, tipo in categorias(con):
        c = consumo.get(nome, 0.0)
        m_ant = round(ant.get(nome, 0.0) / 12, 2) if ant is not None else None
        m_act = round(act.get(nome, 0.0) / meses_completos, 2) if act is not None else None
        linhas.append(dict(categoria=nome, tipo=tipo, mes=round(c, 2), media_ano_anterior=m_ant,
                           media_ano_actual=m_act,
                           falta_ano_anterior=None if m_ant is None else round(m_ant - c, 2),
                           falta_ano_actual=None if m_act is None else round(m_act - c, 2)))
    def total(tipo):
        sel = [l for l in linhas if l['tipo'] == tipo]
        soma = lambda k: None if any(l[k] is None for l in sel) else round(sum(l[k] for l in sel), 2)
        return dict(mes=soma('mes'), media_ano_anterior=soma('media_ano_anterior'),
                    media_ano_actual=soma('media_ano_actual'))
    return dict(data=hoje.isoformat(), meses_completos=meses_completos, tem_ano_anterior=tem_ano_anterior,
                linhas=linhas, total_custos=total('custo'), total_rendimento=total('rendimento'))


def _percentagens(somas, cats):
    custos = [n for n, t in cats if t == 'custo']
    total = round(sum(somas.get(n, 0.0) for n in custos), 2)
    pct = {n: (round(100 * somas.get(n, 0.0) / total, 2) + 0.0 if total else 0.0) for n in custos}
    return total, pct


def anual(con):
    """Vista anual: por ano, custo e % de custos de cada categoria, e rendimento."""
    cats = categorias(con)
    anos = [int(a) for (a,) in con.execute(
        "SELECT DISTINCT substr(data_lanc, 1, 4) FROM movimentos ORDER BY 1")]
    out = []
    for a in anos:
        s = _somas(con, f'{a}-01-01', f'{a}-12-31')
        total, pct = _percentagens(s, cats)
        out.append(dict(periodo=str(a), total_custos=total, rendimento=s.get(_rendimento(cats), 0.0),
                        custos={n: s.get(n, 0.0) for n, t in cats if t == 'custo'}, pct=pct))
    return out


def mensal(con, ano):
    """Vista mensal de um ano: por mês, custo e % de custos de cada categoria, e rendimento."""
    cats = categorias(con)
    out = []
    for m in range(1, 13):
        s = _somas(con, dt.date(ano, m, 1).isoformat(), _fim_mes(ano, m).isoformat())
        if not s:
            continue
        total, pct = _percentagens(s, cats)
        out.append(dict(periodo=f'{ano}-{m:02d}', total_custos=total, rendimento=s.get(_rendimento(cats), 0.0),
                        custos={n: s.get(n, 0.0) for n, t in cats if t == 'custo'}, pct=pct))
    return out


def _rendimento(cats):
    return next(n for n, t in cats if t == 'rendimento')


def _regressao(ys):
    """Recta dos mínimos quadrados; devolve (valor no início, valor no fim, % de evolução)."""
    n = len(ys)
    if n < 2:
        return None
    xm = (n - 1) / 2; ym = sum(ys) / n
    sxx = sum((x - xm) ** 2 for x in range(n))
    b = sum((x - xm) * (y - ym) for x, y in zip(range(n), ys)) / sxx
    a = ym - b * xm
    ini, fim = a, a + b * (n - 1)
    return dict(inicio=round(ini, 2), fim=round(fim, 2), declive=round(b, 4),
                pct=round(100 * (fim - ini) / ini, 1) if ini else None)


def tendencia(con, hoje: dt.date):
    """Custos (total, com Poupança e Investimentos) e rendimento por mês, desde o início dos dados
    até ao último mês completo, com a recta de tendência de cada série."""
    inicio = dt.date.fromisoformat(_meta(con, 'historico_de', '1900-01-01'))
    ate = dt.date(hoje.year, hoje.month, 1) - dt.timedelta(days=1)   # último mês completo
    rows = con.execute('''
        SELECT substr(m.data_lanc, 1, 7),
               SUM(CASE WHEN c.tipo = 'rendimento' THEN m.valor ELSE 0 END),
               SUM(CASE WHEN c.tipo = 'rendimento' THEN 0 ELSE m.valor END)
        FROM movimentos m LEFT JOIN categorias c ON c.id = m.categoria_id
        WHERE m.data_lanc BETWEEN ? AND ? GROUP BY 1 ORDER BY 1''',
                       (inicio.isoformat(), ate.isoformat())).fetchall()
    meses = [r[0] for r in rows]
    rend = [round(r[1], 2) for r in rows]
    custos = [round(-r[2], 2) for r in rows]          # custos em valor positivo
    return dict(meses=meses, rendimento=rend, custos=custos,
                tendencia_rendimento=_regressao(rend), tendencia_custos=_regressao(custos))
