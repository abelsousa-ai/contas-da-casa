"""Regras de negócio: importação de PDFs, sincronização, saldo e análise."""
import base64
import json
import os
import threading
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

import ponte
from db import PASTA_PDFS, guardar_def, ligar, obter_def
from leitor import CATEGORIAS_POR_PREFIXO, CATEGORIA_DESCONHECIDA, ler_pdf, nome_ficheiro

CATEGORIAS = list(CATEGORIAS_POR_PREFIXO.values()) + [CATEGORIA_DESCONHECIDA]
LIMITE_ALERTA_CENT = 100000  # 1.000,00 €

_TRAVA_SYNC = threading.Lock()


class ErroNegocio(Exception):
    pass


def cent(v: Decimal) -> int:
    return int((Decimal(v) * 100).quantize(Decimal("1")))


# ---------- Importação de um PDF ----------

def importar_pdf(con, pdf: bytes, origem: str, email_id=None, drive_file_id=None, drive_url=None) -> dict:
    """Lê o PDF e insere o movimento. Devolve {estado, numero, nome, avisos, id}.
    estado: novo | a_rever | ja_existia | ignorado | erro"""
    r = ler_pdf(pdf)
    if r["tipo"] == "ignorar":
        return {"estado": "ignorado", "numero": None, "nome": None, "avisos": []}
    if r["tipo"] == "desconhecido" or not (r["numero"] and r["data"] and r["valor"]):
        return {"estado": "erro", "numero": r.get("numero"), "nome": None,
                "avisos": r["avisos"] or ["Documento não reconhecido"]}

    nome = nome_ficheiro(r)
    caminho = os.path.join(PASTA_PDFS, nome)
    if not os.path.exists(caminho):
        with open(caminho, "wb") as f:
            f.write(pdf)

    existente = con.execute("SELECT id, drive_url FROM movimentos WHERE tipo = ? AND numero = ?",
                            (r["tipo"], r["numero"])).fetchone()
    if existente:
        # Mesmo documento enviado outra vez (ou já carregado à mão): só completa a ligação ao PDF.
        con.execute("UPDATE movimentos SET pdf_nome = COALESCE(pdf_nome, ?), "
                    "drive_file_id = COALESCE(drive_file_id, ?), drive_url = COALESCE(drive_url, ?), "
                    "email_id = COALESCE(email_id, ?) WHERE id = ?",
                    (nome, drive_file_id, drive_url, email_id, existente["id"]))
        return {"estado": "ja_existia", "numero": r["numero"], "nome": nome, "avisos": [], "id": existente["id"]}

    cur = con.execute(
        "INSERT INTO movimentos (tipo, data, valor_cent, numero, origem, email_id, pdf_nome, "
        "drive_file_id, drive_url, a_rever, avisos) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (r["tipo"], r["data"], cent(r["valor"]), r["numero"], origem, email_id, nome,
         drive_file_id, drive_url, 1 if r["avisos"] else 0, json.dumps(r["avisos"], ensure_ascii=False)))
    mid = cur.lastrowid
    for c in r["categorias"]:
        con.execute("INSERT INTO movimento_categorias (movimento_id, categoria, valor_cent) VALUES (?,?,?)",
                    (mid, c["categoria"], cent(c["valor"])))
    return {"estado": "a_rever" if r["avisos"] else "novo", "numero": r["numero"], "nome": nome,
            "avisos": r["avisos"], "id": mid}


# ---------- Sincronização com o Gmail ----------

def _ponte(con):
    return obter_def(con, "ponte_url"), obter_def(con, "ponte_token")


def sincronizar(tudo: bool = False) -> dict:
    if not _TRAVA_SYNC.acquire(blocking=False):
        raise ErroNegocio("Já está uma sincronização a decorrer.")
    con = ligar()
    try:
        url, token = _ponte(con)
        processados = set() if tudo else {
            x["message_id"] for x in con.execute("SELECT message_id FROM emails_processados")}
        desde = None
        ultimo = None if tudo else con.execute("SELECT MAX(data_email) m FROM emails_processados").fetchone()["m"]
        if ultimo:
            desde = (date.fromisoformat(ultimo[:10]) - timedelta(days=30)).strftime("%Y/%m/%d")

        lista = ponte.chamar(url, token, "listar", desde=desde)["mensagens"]
        resumo = {"emails_novos": 0, "novos": [], "a_rever": [], "ja_existiam": [], "ignorados": 0, "erros": []}

        for m in lista:
            if m["id"] in processados:
                continue
            resumo["emails_novos"] += 1
            obtido = ponte.chamar(url, token, "obter", id=m["id"])
            estados = []
            for a in obtido["anexos"]:
                pdf = base64.b64decode(a["base64"])
                res = importar_pdf(con, pdf, "email", email_id=m["id"],
                                   drive_file_id=a.get("fileId"), drive_url=a.get("url"))
                estados.append(res["estado"])
                if res["estado"] == "novo":
                    resumo["novos"].append(res["numero"])
                elif res["estado"] == "a_rever":
                    resumo["a_rever"].append(res["numero"])
                elif res["estado"] == "ja_existia":
                    resumo["ja_existiam"].append(res["numero"])
                elif res["estado"] == "ignorado":
                    resumo["ignorados"] += 1
                else:
                    resumo["erros"].append(f"{m['assunto']} / {a['nome']}: {'; '.join(res['avisos'])}")
                if res.get("nome") and a.get("fileId"):
                    try:
                        ponte.chamar(url, token, "renomear", fileId=a["fileId"], nome=res["nome"])
                    except ponte.ErroPonte:
                        pass  # o nome no Drive é cosmético; não bloqueia a importação
            con.execute("INSERT INTO emails_processados (message_id, data_email, resultado) VALUES (?,?,?) "
                        "ON CONFLICT(message_id) DO UPDATE SET resultado = excluded.resultado, "
                        "processado_em = datetime('now')",
                        (m["id"], m["data"], ",".join(estados)))
            con.commit()  # email a email: uma falha a meio não perde o que já entrou

        guardar_def(con, "ultima_sync", date.today().isoformat())
        con.commit()
        return resumo
    finally:
        con.close()
        _TRAVA_SYNC.release()


def carregar_manual(pdf: bytes) -> dict:
    """'Carregar PDF': mesmo leitor; guarda também no Drive se a ponte estiver configurada."""
    if not pdf.startswith(b"%PDF"):
        raise ErroNegocio("O ficheiro não é um PDF.")
    con = ligar()
    try:
        r = ler_pdf(pdf)
        drive_id = drive_url = None
        nome = nome_ficheiro(r) if r["tipo"] not in ("ignorar", "desconhecido") else None
        url, token = _ponte(con)
        if nome and url and token:
            try:
                g = ponte.chamar(url, token, "guardar", nome=nome, base64=base64.b64encode(pdf).decode())
                drive_id, drive_url = g.get("fileId"), g.get("url")
            except ponte.ErroPonte:
                pass  # fica pelo menos a cópia local
        res = importar_pdf(con, pdf, "manual", drive_file_id=drive_id, drive_url=drive_url)
        con.commit()
        return res
    finally:
        con.close()


# ---------- Consultas ----------

def listar_movimentos(con) -> list:
    cats = {}
    for c in con.execute("SELECT movimento_id, categoria, valor_cent FROM movimento_categorias ORDER BY categoria"):
        cats.setdefault(c["movimento_id"], []).append({"categoria": c["categoria"], "valor_cent": c["valor_cent"]})
    out = []
    for m in con.execute("SELECT * FROM movimentos ORDER BY data DESC, id DESC"):
        d = dict(m)
        d["avisos"] = json.loads(d["avisos"] or "[]")
        d["categorias"] = cats.get(m["id"], [])
        d["tem_pdf"] = bool(d["pdf_nome"]) and os.path.exists(os.path.join(PASTA_PDFS, d["pdf_nome"]))
        out.append(d)
    return out


def saldo_cent(con) -> int:
    r = con.execute("SELECT COALESCE(SUM(CASE WHEN tipo = 'factura' THEN -valor_cent ELSE valor_cent END), 0) s "
                    "FROM movimentos").fetchone()
    return r["s"]


def ano_escolar(d: date) -> str:
    # Excepção: 2026/2027 começa em Abril 2026 (matrícula paga antes de Setembro).
    if (d.year == 2026 and d.month >= 4) or (d.year == 2027 and d.month <= 7):
        return "2026/2027"
    inicio = d.year if d.month >= 8 else d.year - 1  # Agosto já conta para o ano seguinte
    return f"{inicio}/{inicio + 1}"


def analise(con) -> dict:
    """Consumo = facturas − notas de crédito (saldos não contam)."""
    por_mes, por_ano = OrderedDict(), OrderedDict()
    for m in con.execute("SELECT id, tipo, data, valor_cent FROM movimentos WHERE tipo != 'saldo' ORDER BY data DESC"):
        sinal = 1 if m["tipo"] == "factura" else -1
        d = date.fromisoformat(m["data"])
        mes = m["data"][:7]
        por_mes[mes] = por_mes.get(mes, 0) + sinal * m["valor_cent"]
        ano = por_ano.setdefault(ano_escolar(d), {"total_cent": 0, "categorias": {}})
        ano["total_cent"] += sinal * m["valor_cent"]
        for c in con.execute("SELECT categoria, valor_cent FROM movimento_categorias WHERE movimento_id = ?", (m["id"],)):
            ano["categorias"][c["categoria"]] = ano["categorias"].get(c["categoria"], 0) + sinal * c["valor_cent"]
    for a in por_ano.values():
        a["categorias"] = sorted(({"categoria": k, "valor_cent": v} for k, v in a["categorias"].items()),
                                 key=lambda x: -x["valor_cent"])
    return {"por_mes": [{"mes": k, "valor_cent": v} for k, v in por_mes.items()],
            "por_ano": [{"ano": k, **v} for k, v in por_ano.items()]}


def editar_categorias(con, mid: int, categorias: list, revisto: bool) -> None:
    m = con.execute("SELECT tipo, valor_cent FROM movimentos WHERE id = ?", (mid,)).fetchone()
    if not m:
        raise ErroNegocio("Movimento não encontrado.")
    if m["tipo"] == "saldo":
        if categorias:
            raise ErroNegocio("Saldos não têm categorias.")
    else:
        limpas = []
        for c in categorias:
            if c.get("categoria") not in CATEGORIAS:
                raise ErroNegocio(f"Categoria inválida: {c.get('categoria')}")
            v = int(c.get("valor_cent", 0))
            if v <= 0:
                raise ErroNegocio("Os valores das categorias têm de ser positivos.")
            limpas.append((c["categoria"], v))
        if sum(v for _, v in limpas) != m["valor_cent"]:
            raise ErroNegocio("A soma das categorias tem de ser igual ao valor do documento.")
        con.execute("DELETE FROM movimento_categorias WHERE movimento_id = ?", (mid,))
        con.executemany("INSERT INTO movimento_categorias (movimento_id, categoria, valor_cent) VALUES (?,?,?)",
                        [(mid, c, v) for c, v in limpas])
    if revisto:
        con.execute("UPDATE movimentos SET a_rever = 0 WHERE id = ?", (mid,))
