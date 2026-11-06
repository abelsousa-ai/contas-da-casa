"""Conta Corrente CLIB — aplicação web (Flask) servida pelo ingress do Home Assistant.

Todos os caminhos são RELATIVOS (o ingress acrescenta /api/hassio_ingress/<token>).
"""
import csv
import io
import os
import sys

from flask import Flask, abort, jsonify, request, send_from_directory, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ponte  # noqa: E402
import servico  # noqa: E402
from db import PASTA_PDFS, guardar_def, iniciar, ligar, obter_def  # noqa: E402

VERSAO = os.environ.get("APP_VERSAO", "dev")
GATEWAY_INGRESS = "172.30.32.2"
MODO_DEV = os.environ.get("APP_DEV") == "1"
ESTATICOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
iniciar()


@app.before_request
def so_ingress():
    # Recusar ligações que não venham do gateway do ingress.
    if not MODO_DEV and request.remote_addr != GATEWAY_INGRESS:
        abort(403)


@app.errorhandler(servico.ErroNegocio)
@app.errorhandler(ponte.ErroPonte)
def erro_conhecido(e):
    return jsonify({"erro": str(e)}), 400


@app.get("/")
def pagina():
    resp = send_from_directory(ESTATICOS, "index.html")
    resp.headers["Cache-Control"] = "no-store"  # evita ver versões antigas depois de actualizar
    return resp


@app.get("/api/estado")
def estado():
    con = ligar()
    try:
        return jsonify({
            "versao": VERSAO,
            "saldo_cent": servico.saldo_cent(con),
            "limite_alerta_cent": servico.LIMITE_ALERTA_CENT,
            "movimentos": servico.listar_movimentos(con),
            "categorias": servico.CATEGORIAS,
            "configurado": bool(obter_def(con, "ponte_url") and obter_def(con, "ponte_token")),
            "ultima_sync": obter_def(con, "ultima_sync"),
        })
    finally:
        con.close()


@app.post("/api/sincronizar")
def sincronizar():
    tudo = bool((request.get_json(silent=True) or {}).get("tudo"))
    return jsonify(servico.sincronizar(tudo=tudo))


@app.post("/api/carregar")
def carregar():
    f = request.files.get("pdf")
    if not f:
        raise servico.ErroNegocio("Nenhum ficheiro recebido.")
    return jsonify(servico.carregar_manual(f.read()))


@app.put("/api/movimentos/<int:mid>")
def editar(mid):
    dados = request.get_json(force=True)
    con = ligar()
    try:
        servico.editar_categorias(con, mid, dados.get("categorias", []), bool(dados.get("revisto")))
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.delete("/api/movimentos/<int:mid>")
def apagar(mid):
    con = ligar()
    try:
        con.execute("DELETE FROM movimentos WHERE id = ?", (mid,))
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.get("/api/pdf/<int:mid>")
def pdf(mid):
    con = ligar()
    try:
        m = con.execute("SELECT pdf_nome FROM movimentos WHERE id = ?", (mid,)).fetchone()
    finally:
        con.close()
    if not m or not m["pdf_nome"]:
        abort(404)
    return send_from_directory(PASTA_PDFS, m["pdf_nome"], mimetype="application/pdf")


@app.get("/api/analise")
def analise():
    con = ligar()
    try:
        return jsonify(servico.analise(con))
    finally:
        con.close()


@app.get("/api/exportar.csv")
def exportar():
    con = ligar()
    try:
        movs = servico.listar_movimentos(con)
    finally:
        con.close()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["data", "tipo", "numero", "valor", "origem", "a_rever", "categorias", "pdf"])
    for m in movs:
        w.writerow([m["data"], m["tipo"], m["numero"], f"{m['valor_cent'] / 100:.2f}".replace(".", ","),
                    m["origem"], "sim" if m["a_rever"] else "",
                    " | ".join(f"{c['categoria']}={c['valor_cent'] / 100:.2f}".replace(".", ",") for c in m["categorias"]),
                    m["drive_url"] or ""])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=conta-corrente-clib.csv"})


@app.get("/api/definicoes")
def ler_definicoes():
    con = ligar()
    try:
        return jsonify({"ponte_url": obter_def(con, "ponte_url", ""),
                        "token_definido": bool(obter_def(con, "ponte_token"))})
    finally:
        con.close()


@app.post("/api/definicoes")
def gravar_definicoes():
    dados = request.get_json(force=True)
    url = (dados.get("ponte_url") or "").strip()
    token = (dados.get("ponte_token") or "").strip()
    if not ponte.url_valido(url):
        raise servico.ErroNegocio("URL inválido: tem de ser https://script.google.com/macros/s/…/exec")
    con = ligar()
    try:
        token = token or obter_def(con, "ponte_token")
        ponte.chamar(url, token, "ping", timeout=30)  # só grava se funcionar
        guardar_def(con, "ponte_url", url)
        guardar_def(con, "ponte_token", token)
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()
