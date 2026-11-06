"""Cliente da ponte Gmail/Drive (Google Apps Script, aplicação Web).

A Google responde ao POST em /exec com um redirect 302 para script.googleusercontent.com;
o urllib segue-o como GET (comportamento esperado pela Google).
"""
import json
import re
import urllib.error
import urllib.request

URL_VALIDO = re.compile(r"^https://script\.google\.com/macros/s/[\w-]+/exec$")


class ErroPonte(Exception):
    pass


def url_valido(url: str) -> bool:
    return bool(url and URL_VALIDO.match(url.strip()))


def chamar(url: str, token: str, accao: str, timeout: int = 180, **params) -> dict:
    if not url or not token:
        raise ErroPonte("Ponte não configurada (Definições).")
    corpo = json.dumps({"token": token, "accao": accao, **params}).encode("utf-8")
    pedido = urllib.request.Request(url, data=corpo, method="POST",
                                    headers={"Content-Type": "text/plain;charset=utf-8"})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as r:
            bruto = r.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise ErroPonte(f"Sem ligação à ponte: {e}") from e
    try:
        dados = json.loads(bruto)
    except ValueError as e:
        # Normalmente: página de login/erro da Google (implementação sem acesso "Qualquer pessoa").
        raise ErroPonte("Resposta inválida da ponte (confirma a implementação e o acesso).") from e
    if "erro" in dados:
        raise ErroPonte({"nao_autorizado": "Token errado."}.get(dados["erro"], dados["erro"]))
    return dados
