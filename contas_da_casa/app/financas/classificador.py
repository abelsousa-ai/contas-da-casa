"""Categorização automática (R1).

Princípio: só classifica sozinho quando tem certeza, nível a nível
(Categoria, Subcategoria, Pessoa). Ordem de decisão:
  1. regra fixa de classificação (ou de pessoa por data);
  2. lista "perguntar sempre" -> fica pendente;
  3. histórico: a descrição já apareceu e teve sempre o mesmo valor nesse nível.
A subcategoria pode ficar em branco; sem Categoria ou sem Pessoa o movimento fica pendente.
"""
import json, re, datetime as dt
from dataclasses import dataclass
from .feriados import penultimo_dia_util


@dataclass
class Regra:
    id: int; ordem: int; tipo: str; conta: str | None; padrao: re.Pattern
    categoria_id: int | None; subcategoria_id: int | None; pessoa_id: int | None
    config: dict; nota: str | None


@dataclass
class Resultado:
    categoria_id: int | None = None
    subcategoria_id: int | None = None
    pessoa_id: int | None = None
    nota: str = ''

    @property
    def estado(self):
        return 'ok' if self.categoria_id and self.pessoa_id else 'pendente'


def carregar_regras(con):
    out = []
    for r in con.execute('''SELECT id, ordem, tipo, conta_codigo, padrao, categoria_id, subcategoria_id,
                            pessoa_id, config, nota FROM regras WHERE activa = 1 ORDER BY ordem, id'''):
        out.append(Regra(r[0], r[1], r[2], r[3], re.compile(r[4]), r[5], r[6], r[7],
                         json.loads(r[8]) if r[8] else {}, r[9]))
    return out


def regra_para(regras, conta, descricao, tipos):
    d = descricao.upper()
    for r in regras:
        if r.tipo in tipos and (r.conta is None or r.conta == conta) and r.padrao.search(d):
            return r
    return None


class Classificador:
    def __init__(self, con):
        self.con = con
        self.regras = carregar_regras(con)
        self.pessoa_id = dict(con.execute('SELECT nome, id FROM pessoas'))
        r = con.execute("SELECT valor FROM meta WHERE chave = 'aberto_de'").fetchone()
        self.aprende_de = r[0] if r else '1900-01-01'   # só aprende com os anos abertos (2026 em diante)

    def historico(self, descricao_norm):
        return self.con.execute('''SELECT categoria_id, subcategoria_id, pessoa_id FROM movimentos
                                   WHERE descricao_norm = ? AND aprende = 1 AND estado = 'ok'
                                   AND origem != 'previsto' AND data_lanc >= ?''',
                                (descricao_norm, self.aprende_de)).fetchall()

    def _pessoa_por_data(self, regra, data):
        pen = penultimo_dia_util(data.year, data.month)
        if data == pen:
            return self.pessoa_id.get(regra.config.get('penultimo_dia_util'))
        if data < pen:
            return self.pessoa_id.get(regra.config.get('antes'))
        return None

    def classificar(self, conta, descricao, descricao_norm, data_lanc: dt.date) -> Resultado:
        res = Resultado()
        regra = regra_para(self.regras, conta, descricao, ('classificar', 'pessoa_por_data'))
        if regra:
            res.categoria_id, res.subcategoria_id, res.pessoa_id = (
                regra.categoria_id, regra.subcategoria_id, regra.pessoa_id)
            if regra.tipo == 'pessoa_por_data':
                res.pessoa_id = self._pessoa_por_data(regra, data_lanc)
            res.nota = f'Regra: {regra.nota}'
        else:
            pergunta = regra_para(self.regras, conta, descricao, ('perguntar',))
            if pergunta:
                res.nota = f'Perguntar sempre: {pergunta.nota}'
                return res
        hist = self.historico(descricao_norm)
        if not hist:
            if not regra:
                res.nota = 'Descrição nova (sem histórico)'
            return self._fechar(res)
        if res.categoria_id is None:
            cats = {h[0] for h in hist}
            if len(cats) == 1:
                res.categoria_id = cats.pop()
                res.nota = 'Histórico'
        if res.categoria_id is not None:
            mesma_cat = [h for h in hist if h[0] == res.categoria_id]
            if mesma_cat:
                if res.subcategoria_id is None:
                    subs = {h[1] for h in mesma_cat}
                    if len(subs) == 1:
                        res.subcategoria_id = subs.pop()
                if res.pessoa_id is None and not (regra and regra.tipo == 'pessoa_por_data'):
                    pes = {h[2] for h in mesma_cat}
                    if len(pes) == 1:
                        res.pessoa_id = pes.pop()
        return self._fechar(res)

    @staticmethod
    def _fechar(res):
        falta = [n for n, v in (('categoria', res.categoria_id), ('pessoa', res.pessoa_id)) if v is None]
        if falta:
            res.nota = (res.nota + '; ' if res.nota else '') + 'em dúvida: ' + ', '.join(falta)
        return res
