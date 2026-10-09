"""Importação de extractos: leitura, exclusões, duplicados, conciliação e classificação."""
import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from . import leitores
from .classificador import Classificador, regra_para
from .normalizar import norm_desc

MARGEM_PREVISTO = 5      # dias: conciliação e assinalar previstos (valor por defeito)
MARGEM_TRANSFER = 3      # dias: outra ponta de uma transferência interna


class ErroImportacao(Exception):
    pass


@dataclass
class Resumo:
    ficheiro: str
    conta: str = ''
    periodo: str = ''
    lidos: int = 0
    fora_periodo: int = 0        # anteriores a 'aberto_de' (anos fechados): ignorados
    anteriores: int = 0          # já cobertos pelo histórico
    em_falta_no_historico: int = 0  # data dentro do histórico mas não estavam lá (ficam pendentes)
    por_concluir: int = 0        # Revolut: ainda não concluídos (entram num extracto futuro)
    duplicados: int = 0
    excluidos: int = 0
    novos: int = 0
    automaticos: int = 0
    pendentes: int = 0
    conciliados: int = 0
    previstos_assinalados: int = 0
    transferencias_a_verificar: int = 0
    pendentes_ids: list = field(default_factory=list)
    excluidos_detalhe: list = field(default_factory=list)


def _meta(con, chave, defeito=None):
    r = con.execute('SELECT valor FROM meta WHERE chave = ?', (chave,)).fetchone()
    return r[0] if r else defeito


def _conta(con, ex):
    if ex.formato == 'revolut':
        codigo = 'revolut'
    else:
        r = con.execute('SELECT codigo FROM contas WHERE numero = ?', (ex.numero,)).fetchone()
        if not r:
            raise ErroImportacao(f'O extracto é da conta/cartão nº {ex.numero}, que não está configurada')
        codigo = r[0]
    cid = con.execute('SELECT id FROM contas WHERE codigo = ?', (codigo,)).fetchone()[0]
    return codigo, cid


def _chave(l):
    return (l.data_lanc.isoformat(), l.data_valor.isoformat() if l.data_valor else None,
            l.descricao, round(l.valor, 2), None if l.saldo is None else round(l.saldo, 2), l.hora)


def _existentes(con, conta_id, de, ate):
    """Contagem de movimentos (e excluídos) já guardados desta conta no período do ficheiro."""
    c = Counter()
    q = '''SELECT data_lanc, data_valor, descricao, valor, saldo, hora FROM {t}
           WHERE conta_id = ? AND data_lanc BETWEEN ? AND ? {extra}'''
    for t, extra in (('movimentos', "AND origem = 'extracto'"), ('movimentos_excluidos', '')):
        for r in con.execute(q.format(t=t, extra=extra), (conta_id, de, ate)):
            c[(r[0], r[1], r[2], round(r[3], 2), None if r[4] is None else round(r[4], 2), r[5])] += 1
    return c


def _previstos(con, conta_id, valor, data):
    de = (data - dt.timedelta(days=MARGEM_PREVISTO)).isoformat()
    ate = (data + dt.timedelta(days=MARGEM_PREVISTO)).isoformat()
    return con.execute('''SELECT id, categoria_id, subcategoria_id, pessoa_id, descricao FROM movimentos
                          WHERE origem = 'previsto' AND conta_id = ? AND round(valor, 2) = ?
                          AND data_lanc BETWEEN ? AND ?''', (conta_id, round(valor, 2), de, ate)).fetchall()


def importar(con, nome_ficheiro, conteudo: bytes, agora=None) -> Resumo:
    agora = agora or dt.datetime.now().isoformat(timespec='seconds')
    ex = leitores.ler(nome_ficheiro, conteudo)
    conta, conta_id = _conta(con, ex)
    res = Resumo(nome_ficheiro, conta, f'{ex.periodo_de} a {ex.periodo_ate}', lidos=len(ex.linhas))
    hist_ate = dt.date.fromisoformat(_meta(con, 'historico_ate', '1900-01-01'))
    # anos fechados: tudo o que é anterior a 'aberto_de' é ignorado (os dados antigos ficam como estão)
    hist_de = dt.date.fromisoformat(_meta(con, 'aberto_de', _meta(con, 'historico_de', '1900-01-01')))
    clf = Classificador(con)
    sinal = -1 if ex.formato == 'activobank_cartao' else 1

    datas = [l.data_lanc for l in ex.linhas] or [dt.date.today()]
    existentes = _existentes(con, conta_id, min(datas).isoformat(), max(datas).isoformat())
    vistos = Counter()
    # movimentos do histórico desta conta no período do ficheiro, por (data, valor)
    no_hist = Counter((r[0], round(r[1], 2)) for r in con.execute(
        '''SELECT data_lanc, valor FROM movimentos WHERE conta_id = ? AND origem = 'historico'
           AND data_lanc BETWEEN ? AND ?''', (conta_id, min(datas).isoformat(), max(datas).isoformat())))
    vistos_hist = Counter()

    cur = con.execute('''INSERT INTO importacoes(conta_id, ficheiro, periodo_de, periodo_ate, importado_em)
                         VALUES (?,?,?,?,?)''', (conta_id, nome_ficheiro, str(ex.periodo_de), str(ex.periodo_ate), agora))
    imp_id = cur.lastrowid
    anterior = None   # último movimento inserido (para taxas/comissões)

    for l in ex.linhas:
        l.valor = round(sinal * l.valor, 2)
        falta_hist = False
        if l.data_lanc < hist_de:
            res.fora_periodo += 1
            continue
        if l.data_lanc <= hist_ate:
            kh = (l.data_lanc.isoformat(), l.valor); vistos_hist[kh] += 1
            if vistos_hist[kh] <= no_hist[kh]:
                res.anteriores += 1
                continue
            falta_hist = True   # data coberta pelo histórico, mas o movimento não estava lá
        if ex.formato == 'revolut' and l.estado not in ('CONCLUÍDA', 'REVERTIDA'):
            res.por_concluir += 1
            continue
        k = _chave(l); vistos[k] += 1
        if vistos[k] <= existentes[k]:
            res.duplicados += 1
            continue

        motivo, regra = None, None
        if ex.formato == 'revolut' and l.estado == 'REVERTIDA':
            motivo = 'Revertida (Revolut)'
        else:
            regra = regra_para(clf.regras, conta, l.descricao, ('excluir',))
            if regra: motivo = regra.nota
        if motivo:
            con.execute('''INSERT INTO movimentos_excluidos(importacao_id, conta_id, data_lanc, data_valor, hora,
                           descricao, valor, saldo, motivo, regra_id, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (imp_id, conta_id, l.data_lanc.isoformat(), l.data_valor and l.data_valor.isoformat(),
                         l.hora, l.descricao, l.valor, l.saldo, motivo, regra and regra.id, agora))
            res.excluidos += 1
            res.excluidos_detalhe.append((l.data_lanc.isoformat(), l.descricao, l.valor, motivo))
            continue

        dnorm = norm_desc(l.descricao)
        obs, nota = None, ''
        cat = sub = pes = None

        taxa = (l.tipo == 'Comissão') or regra_para(clf.regras, conta, l.descricao, ('taxa_compra_anterior',))
        if taxa:
            ok = anterior is not None and (l.tipo == 'Comissão' or anterior['data_valor'] == l.data_lanc.isoformat())
            if ok:
                cat, sub, pes = anterior['cat'], anterior['sub'], anterior['pes']
                nota = f"Taxa da compra #{anterior['id']}"
            else:
                nota = 'Taxa sem compra correspondente na linha anterior'
        else:
            cands = _previstos(con, conta_id, l.valor, l.data_lanc)
            if len(cands) == 1:
                p = cands[0]
                cat, sub, pes, obs = p[1], p[2], p[3], p[4]
                con.execute('DELETE FROM movimentos WHERE id = ?', (p[0],))
                nota = 'Conciliado com movimento previsto'
                res.conciliados += 1
            elif len(cands) > 1:
                nota = f'Escolher o movimento previsto correspondente ({len(cands)} candidatos)'
            else:
                r = clf.classificar(conta, l.descricao, dnorm, l.data_lanc)
                cat, sub, pes, nota = r.categoria_id, r.subcategoria_id, r.pessoa_id, r.nota

        estado = 'ok' if (cat and pes and 'candidatos' not in nota and not falta_hist) else 'pendente'
        if falta_hist:
            nota = 'Data dentro do histórico, mas não estava no Excel: confirmar ou apagar; ' + nota
            res.em_falta_no_historico += 1
        falta = [n for n, v in (('categoria', cat), ('pessoa', pes)) if not v]
        if falta and 'em dúvida' not in nota and 'candidatos' not in nota and not nota.startswith('Perguntar'):
            nota = (nota + '; ' if nota else '') + 'em dúvida: ' + ', '.join(falta)
        cur = con.execute('''INSERT INTO movimentos(conta_id, data_lanc, data_valor, hora, descricao, descricao_norm, valor,
                             saldo, categoria_id, subcategoria_id, pessoa_id, origem, estado, aprende, importacao_id,
                             observacoes, nota_sistema, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,'extracto',?,1,?,?,?,?)''',
                          (conta_id, l.data_lanc.isoformat(), l.data_valor and l.data_valor.isoformat(), l.hora,
                           l.descricao, dnorm, l.valor, l.saldo, cat, sub, pes, estado, imp_id, obs, nota, agora))
        if not taxa:
            anterior = dict(id=cur.lastrowid, data_valor=l.data_valor and l.data_valor.isoformat(),
                            cat=cat, sub=sub, pes=pes)
        res.novos += 1
        if estado == 'ok': res.automaticos += 1
        else: res.pendentes += 1; res.pendentes_ids.append(cur.lastrowid)

    # previstos desta conta que já deviam ter aparecido no extracto
    if ex.periodo_ate:
        limite = (ex.periodo_ate - dt.timedelta(days=MARGEM_PREVISTO)).isoformat()
        res.previstos_assinalados = con.execute(
            '''UPDATE movimentos SET estado = 'assinalado' WHERE origem = 'previsto' AND conta_id = ?
               AND data_lanc <= ? AND estado != 'assinalado' ''', (conta_id, limite)).rowcount
    res.transferencias_a_verificar = verificar_transferencias(con)
    con.execute('''UPDATE importacoes SET n_lidos=?, n_anteriores=?, n_duplicados=?, n_excluidos=?, n_novos=?,
                   n_automaticos=?, n_pendentes=?, n_conciliados=? WHERE id=?''',
                (res.lidos, res.anteriores, res.duplicados, res.excluidos, res.novos, res.automaticos,
                 res.pendentes, res.conciliados, imp_id))
    con.commit()
    return res


def verificar_transferencias(con):
    """Assinala transferências internas excluídas cuja outra ponta falta, quando a outra conta
    já tem um extracto importado que cobre a data. Devolve quantas estão por verificar."""
    import json
    cfg = {r[0]: json.loads(r[1]) for r in con.execute(
        "SELECT id, config FROM regras WHERE tipo = 'excluir' AND config IS NOT NULL")}
    conta_id = dict(con.execute('SELECT codigo, id FROM contas'))
    n = 0
    for eid, data, valor, regra_id in con.execute(
            'SELECT id, data_lanc, valor, regra_id FROM movimentos_excluidos WHERE regra_id IS NOT NULL').fetchall():
        outra = cfg.get(regra_id, {}).get('outra_conta')
        if not outra:
            continue
        oid = conta_id[outra]
        coberta = con.execute('''SELECT 1 FROM importacoes WHERE conta_id = ? AND periodo_de <= ? AND periodo_ate >= ?''',
                              (oid, data, data)).fetchone()
        if not coberta:
            flag = 0
        else:
            d = dt.date.fromisoformat(data)
            ponta = con.execute('''SELECT 1 FROM movimentos_excluidos WHERE conta_id = ? AND round(valor, 2) = ?
                                   AND data_lanc BETWEEN ? AND ?''',
                                (oid, round(-valor, 2), (d - dt.timedelta(days=MARGEM_TRANSFER)).isoformat(),
                                 (d + dt.timedelta(days=MARGEM_TRANSFER)).isoformat())).fetchone()
            flag = 0 if ponta else 1
        con.execute('UPDATE movimentos_excluidos SET verificar = ? WHERE id = ?', (flag, eid))
        n += flag
    return n
