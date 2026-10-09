"""Interface web. Corre dentro do add-on e é servida pelo ingress do Home Assistant."""
import datetime as dt, os, sqlite3, sys
from flask import Flask, g, render_template, request, redirect, url_for, flash, send_file, abort, jsonify
import io

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from financas import analises, operacoes                       # noqa: E402
from financas.importador import importar, ErroImportacao        # noqa: E402
from financas.leitores import FormatoDesconhecido               # noqa: E402

DB = os.environ.get('FINANCAS_DB', '/data/financas.sqlite')
INVERTIDAS = {'Poupança', 'Investimentos', 'Vencimentos & outros ganhos'}
CORES_CATEGORIA = {
    'Automóvel': '#4E79A7', 'Compras Gerais (Alimentação & Casa)': '#F28E2B',
    'Despesas com Lazer & restaurantes': '#E15759', 'Educação': '#76B7B2', 'Férias': '#59A14F',
    'Habitação': '#EDC948', 'Investimentos': '#B07AA1', 'Limpeza da casa': '#FF9DA7',
    'Ofertas/Prendas': '#9C755F', 'Poupança': '#BAB0AC', 'Roupa (vestuário)': '#1F6F78',
    'Saúde': '#8CD17D', 'Utilitários': '#86BCB6', 'Z_Outros': '#79706E', 'Por classificar': '#D4A6C8'}


IP_PERMITIDO = os.environ.get('FINANCAS_IP_PERMITIDO', '172.30.32.2')   # gateway do ingress; '*' só em testes


class Ingress:
    """Só aceita pedidos do gateway do ingress do Home Assistant (que já autenticou o utilizador)
    e usa o prefixo que ele indica no cabeçalho X-Ingress-Path."""
    def __init__(self, app): self.app = app
    def __call__(self, environ, start_response):
        if IP_PERMITIDO != '*' and environ.get('REMOTE_ADDR') != IP_PERMITIDO:
            start_response('403 Forbidden', [('Content-Type', 'text/plain; charset=utf-8')])
            return ['Acesso só através do Home Assistant.'.encode()]
        prefixo = environ.get('HTTP_X_INGRESS_PATH', '')
        if prefixo:
            environ['SCRIPT_NAME'] = prefixo.rstrip('/')
        return self.app(environ, start_response)


def _segredo():
    """Chave das sessões (mensagens), guardada na pasta de dados para sobreviver a reinícios."""
    caminho = os.environ.get('FINANCAS_SEGREDO_FICHEIRO')
    if not caminho:
        return os.urandom(24)
    if not os.path.exists(caminho):
        with open(caminho, 'wb') as f:
            f.write(os.urandom(32))
        os.chmod(caminho, 0o600)
    return open(caminho, 'rb').read()


app = Flask(__name__)
app.secret_key = _segredo()
app.wsgi_app = Ingress(app.wsgi_app)
app.config['MAX_CONTENT_LENGTH'] = 60 * 1024 * 1024


@app.before_request
def precisa_de_base():
    """Sem base de dados, a única página disponível é a de carregar a base inicial."""
    if not os.path.exists(DB) and request.endpoint not in ('configurar', 'static'):
        return redirect(url_for('configurar'))


@app.route('/configurar', methods=['GET', 'POST'])
def configurar():
    if os.path.exists(DB):
        return redirect(url_for('inicio'))
    erro = None
    if request.method == 'POST':
        f = request.files.get('base')
        dados = f.read() if f else b''
        if not dados.startswith(b'SQLite format 3'):
            erro = 'O ficheiro não é uma base de dados SQLite.'
        else:
            tmp = DB + '.novo'
            with open(tmp, 'wb') as out:
                out.write(dados)
            try:
                c = sqlite3.connect(tmp)
                versao = c.execute("SELECT valor FROM meta WHERE chave = 'versao_esquema'").fetchone()
                n = c.execute('SELECT COUNT(*) FROM movimentos').fetchone()[0]
                c.close()
                if not versao:
                    raise sqlite3.DatabaseError('sem versão')
                os.replace(tmp, DB)
                flash(f'Base de dados carregada: {n} movimentos.')
                return redirect(url_for('inicio'))
            except sqlite3.DatabaseError:
                os.remove(tmp)
                erro = 'O ficheiro não é a base de dados desta aplicação.'
    return render_template('configurar.html', erro=erro)


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB)
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def fechar(_):
    c = g.pop('db', None)
    if c: c.close()


def hoje():
    d = request.args.get('data')
    try:
        return dt.date.fromisoformat(d) if d else dt.date.today()
    except ValueError:
        return dt.date.today()


def avisos():
    c = db()
    return dict(
        pendentes=c.execute("SELECT COUNT(*) FROM movimentos WHERE estado = 'pendente'").fetchone()[0],
        assinalados=c.execute("SELECT COUNT(*) FROM movimentos WHERE estado = 'assinalado'").fetchone()[0],
        previstos=c.execute("SELECT COUNT(*) FROM movimentos WHERE origem = 'previsto'").fetchone()[0],
        transferencias=c.execute('SELECT COUNT(*) FROM movimentos_excluidos WHERE verificar = 1').fetchone()[0])


@app.context_processor
def comum():
    vazio = dict(pendentes=0, assinalados=0, previstos=0, transferencias=0)
    return dict(avisos=avisos() if os.path.exists(DB) else vazio, euro=euro, cor_cat=CORES_CATEGORIA)


def euro(v, sinal=True):
    if v is None:
        return '—'
    s = f'{abs(v):,.2f}'.replace(',', ' ').replace('.', ',')
    return ('−' if v < 0 and sinal else '') + s + ' €'


# ---------------------------------------------------------------- mês corrente

def _cor(pct, invertida):
    if pct is None: return None
    if not invertida: return 'bom' if pct < 80 else ('perto' if pct <= 100 else 'mau')
    return 'mau' if pct < 80 else ('perto' if pct <= 100 else 'bom')


def _linha(nome, tipo, mes, med_act, med_ant, invertida):
    pct = None
    sinal_ok = med_ant is not None and med_ant != 0 and ((tipo == 'custo' and med_ant < 0) or (tipo == 'rendimento' and med_ant > 0))
    if sinal_ok:
        pct = round(100 * abs(mes) / abs(med_ant))
    cor_media = None
    if med_act is not None and sinal_ok and abs(med_act) != abs(med_ant):
        maior = abs(med_act) > abs(med_ant)
        cor_media = ('bom' if maior else 'mau') if invertida else ('mau' if maior else 'bom')
    return dict(categoria=nome, mes=mes, pct=pct, cor=_cor(pct, invertida), media_ano=med_act,
                media_anterior=med_ant, cor_media=cor_media)


@app.route('/')
def inicio():
    v = analises.mes_corrente(db(), hoje())
    t = v['total_custos']
    total = _linha('Total de custos', 'custo', t['mes'], t['media_ano_actual'], t['media_ano_anterior'], False)
    return render_template('inicio.html', v=v, total=total, data=hoje())


@app.route('/mes')
def mes():
    d = hoje()
    v = analises.mes_corrente(db(), d)
    t = v['total_custos']
    total = _linha('Total de custos', 'custo', t['mes'], t['media_ano_actual'], t['media_ano_anterior'], False)
    linhas = [_linha(l['categoria'], l['tipo'], l['mes'], l['media_ano_actual'], l['media_ano_anterior'],
                     l['categoria'] in INVERTIDAS)
              for l in v['linhas'] if not (l['mes'] == 0 and (l['categoria'] in ('Z_Outros', analises.POR_CLASSIFICAR)))]
    linhas.sort(key=lambda l: (l['pct'] is None, -(l['pct'] or 0)))
    return render_template('mes.html', total=total, linhas=linhas, data=d, v=v)


# ---------------------------------------------------------------- análises

@app.route('/analises')
def analises_pagina():
    c = db()
    anos = [r[0] for r in c.execute("SELECT DISTINCT substr(data_lanc,1,4) FROM movimentos ORDER BY 1")]
    return render_template('analises.html', anos=anos, ano=request.args.get('ano', anos[-1] if anos else ''),
                           vista=request.args.get('vista', 'mensal'))


@app.route('/api/mensal/<int:ano>')
def api_mensal(ano):
    return jsonify(_series(analises.mensal(db(), ano)))


@app.route('/api/anual')
def api_anual():
    dados = analises.anual(db())
    out = _series(dados)
    queijos = []
    for x in dados:
        gastos = {k: -v for k, v in x['custos'].items() if v < 0}
        fora = {k: v for k, v in x['custos'].items() if v > 0}
        soma = sum(gastos.values())
        queijos.append(dict(periodo=x['periodo'], fatias=[dict(categoria=k, valor=round(v, 2),
                       pct=round(100 * v / soma, 1)) for k, v in sorted(gastos.items(), key=lambda kv: -kv[1])],
                       fora=[dict(categoria=k, valor=v) for k, v in fora.items()]))
    out['queijos'] = queijos
    return jsonify(out)


@app.route('/api/tendencia')
def api_tendencia():
    return jsonify(analises.tendencia(db(), hoje()))


def _series(periodos):
    cats = [n for n, t in analises.categorias(db()) if t == 'custo']
    usadas = [cname for cname in cats if any(p['custos'].get(cname) for p in periodos)]
    return dict(periodos=[p['periodo'] for p in periodos],
                categorias=[dict(nome=cn, cor=CORES_CATEGORIA.get(cn, '#888'),
                                 valores=[round(-p['custos'].get(cn, 0.0), 2) for p in periodos]) for cn in usadas],
                total=[round(-p['total_custos'], 2) for p in periodos])


# ---------------------------------------------------------------- importar

@app.route('/importar', methods=['GET', 'POST'])
def importar_pagina():
    resumos, erros = [], []
    if request.method == 'POST':
        for f in request.files.getlist('ficheiros'):
            if not f.filename:
                continue
            try:
                resumos.append(importar(db(), f.filename, f.read()))
            except (ErroImportacao, FormatoDesconhecido, ValueError) as e:
                db().rollback(); erros.append((f.filename, str(e)))
        if not resumos and not erros:
            erros.append(('', 'Escolhe pelo menos um ficheiro.'))
    ids = [i for r in resumos for i in r.pendentes_ids]
    return render_template('importar.html', resumos=resumos, erros=erros,
                           perguntas=_pendentes(ids) if ids else [], **operacoes.listas(db()))


# ---------------------------------------------------------------- pendentes e classificação

def _pendentes(ids=None):
    c = db()
    q = '''SELECT m.id, m.data_lanc, co.nome, m.descricao, m.descricao_norm, m.valor, m.categoria_id, m.subcategoria_id,
                  m.pessoa_id, m.nota_sistema FROM movimentos m JOIN contas co ON co.id = m.conta_id
           WHERE m.estado = 'pendente' '''
    args = []
    if ids:
        q += f" AND m.id IN ({','.join('?' * len(ids))})"; args = ids
    out = []
    for r in c.execute(q + ' ORDER BY m.data_lanc, m.id', args).fetchall():
        p = dict(id=r[0], data=r[1], conta=r[2], descricao=r[3], valor=r[5], categoria_id=r[6], subcategoria_id=r[7],
                 pessoa_id=r[8], nota=r[9] or '')
        sug = operacoes.sugestao(c, r[4])
        if sug:
            for k in ('categoria_id', 'subcategoria_id', 'pessoa_id'):
                if p[k] is None: p[k] = sug[k]
            p['sugestao'] = sug
        p['iguais'] = c.execute("SELECT COUNT(*) FROM movimentos WHERE descricao_norm = ? AND estado = 'pendente' AND id != ?",
                                (r[4], r[0])).fetchone()[0]
        p['candidatos'] = operacoes.candidatos_previsto(c, r[0]) if 'candidatos' in p['nota'] else []
        out.append(p)
    return out


@app.route('/pendentes')
def pendentes():
    c = db()
    previstos = c.execute('''SELECT m.id, m.data_lanc, co.nome, m.descricao, m.valor, m.estado FROM movimentos m
                             JOIN contas co ON co.id = m.conta_id WHERE m.origem = 'previsto' ORDER BY m.data_lanc''').fetchall()
    transf = c.execute('''SELECT e.data_lanc, co.nome, e.descricao, e.valor, e.motivo FROM movimentos_excluidos e
                          JOIN contas co ON co.id = e.conta_id WHERE e.verificar = 1 ORDER BY e.data_lanc''').fetchall()
    return render_template('pendentes.html', perguntas=_pendentes(), previstos=previstos, transferencias=transf,
                           **operacoes.listas(c))


def _int(v):
    try: return int(v) if v not in (None, '') else None
    except ValueError: return None


@app.post('/classificar/<int:mov_id>')
def classificar(mov_id):
    n = operacoes.classificar(db(), mov_id, _int(request.form.get('categoria')), _int(request.form.get('subcategoria')),
                              _int(request.form.get('pessoa')), bool(request.form.get('mesma_descricao')))
    flash(f'Classificado ({n} movimento{"s" if n > 1 else ""}).')
    return redirect(request.form.get('voltar') or url_for('pendentes'))


@app.post('/conciliar/<int:mov_id>/<int:previsto_id>')
def conciliar(mov_id, previsto_id):
    operacoes.conciliar(db(), mov_id, previsto_id)
    flash('Conciliado com o movimento previsto.')
    return redirect(request.form.get('voltar') or url_for('pendentes'))


@app.post('/apagar/<int:mov_id>')
def apagar(mov_id):
    try:
        operacoes.apagar(db(), mov_id); flash('Movimento apagado.')
    except ValueError as e:
        flash(str(e), 'erro')
    return redirect(request.form.get('voltar') or url_for('movimentos'))


# ---------------------------------------------------------------- movimento previsto

@app.route('/previsto', methods=['GET', 'POST'])
def previsto():
    c = db()
    if request.method == 'POST':
        f = request.form
        try:
            valor = float(f.get('valor', '0').replace(',', '.'))
            valor = -abs(valor) if f.get('sentido', 'saida') == 'saida' else abs(valor)
            operacoes.criar_previsto(c, _int(f.get('conta')), f.get('data'), valor, f.get('descricao'),
                                     _int(f.get('categoria')), _int(f.get('subcategoria')), _int(f.get('pessoa')))
            flash('Movimento previsto registado. Já conta nos números do mês.')
            return redirect(url_for('previsto'))
        except ValueError as e:
            flash(str(e), 'erro')
    previstos = c.execute('''SELECT m.id, m.data_lanc, co.nome, m.descricao, m.valor, m.estado, ca.nome, p.nome
                             FROM movimentos m JOIN contas co ON co.id = m.conta_id
                             LEFT JOIN categorias ca ON ca.id = m.categoria_id LEFT JOIN pessoas p ON p.id = m.pessoa_id
                             WHERE m.origem = 'previsto' ORDER BY m.data_lanc DESC''').fetchall()
    return render_template('previsto.html', previstos=previstos, hoje=dt.date.today().isoformat(), **operacoes.listas(c))


# ---------------------------------------------------------------- movimentos

@app.route('/movimentos')
def movimentos():
    c = db(); a = request.args
    q = '''SELECT m.id, m.data_lanc, co.nome, m.descricao, m.valor, m.categoria_id, m.subcategoria_id, m.pessoa_id,
                  m.origem, m.estado, m.observacoes FROM movimentos m JOIN contas co ON co.id = m.conta_id WHERE 1=1'''
    args = []
    if a.get('texto'):
        q += ' AND (m.descricao LIKE ? OR m.observacoes LIKE ?)'; args += [f"%{a['texto']}%"] * 2
    if a.get('conta'):
        q += ' AND m.conta_id = ?'; args.append(_int(a['conta']))
    if a.get('categoria'):
        q += ' AND m.categoria_id = ?'; args.append(_int(a['categoria']))
    if a.get('de'):
        q += ' AND m.data_lanc >= ?'; args.append(a['de'])
    if a.get('ate'):
        q += ' AND m.data_lanc <= ?'; args.append(a['ate'])
    total = c.execute(q.replace('SELECT m.id, m.data_lanc, co.nome, m.descricao, m.valor, m.categoria_id, m.subcategoria_id, m.pessoa_id,\n                  m.origem, m.estado, m.observacoes', 'SELECT COUNT(*), COALESCE(SUM(m.valor),0)'), args).fetchone()
    linhas = c.execute(q + ' ORDER BY m.data_lanc DESC, m.id DESC LIMIT 200', args).fetchall()
    return render_template('movimentos.html', linhas=linhas, total=total, filtro=a, **operacoes.listas(c))


@app.route('/movimento/<int:mov_id>')
def movimento(mov_id):
    c = db()
    m = c.execute('''SELECT m.id, m.data_lanc, co.nome, m.descricao, m.valor, m.categoria_id, m.subcategoria_id, m.pessoa_id,
                     m.origem, m.estado, m.observacoes, m.nota_sistema FROM movimentos m JOIN contas co ON co.id = m.conta_id
                     WHERE m.id = ?''', (mov_id,)).fetchone()
    if not m:
        abort(404)
    return render_template('movimento.html', m=m, voltar=request.args.get('voltar') or url_for('movimentos'),
                           **operacoes.listas(c))


# ---------------------------------------------------------------- listas e exportar

@app.route('/listas', methods=['GET', 'POST'])
def listas():
    c = db()
    if request.method == 'POST':
        f = request.form
        try:
            if f.get('accao') == 'acrescentar':
                operacoes.acrescentar(c, f.get('tabela'), f.get('nome'), _int(f.get('categoria')))
                flash(f'"{f.get("nome").strip()}" acrescentado.')
            elif f.get('accao') in ('activar', 'desactivar'):
                operacoes.activar(c, f.get('tabela'), _int(f.get('id')), f.get('accao') == 'activar')
        except (ValueError, sqlite3.IntegrityError) as e:
            flash('Já existe um elemento com esse nome.' if isinstance(e, sqlite3.IntegrityError) else str(e), 'erro')
        return redirect(url_for('listas'))
    return render_template('listas.html', **operacoes.listas(c))


@app.route('/exportar')
def exportar():
    return render_template('exportar.html')


@app.route('/exportar/ficheiro')
def exportar_ficheiro():
    dados = operacoes.exportar_xlsx(db())
    nome = f'financas_{dt.date.today().isoformat()}.xlsx'
    return send_file(io.BytesIO(dados), as_attachment=True, download_name=nome,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORTA', 8099)))
