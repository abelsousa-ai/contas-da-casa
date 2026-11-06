"""Leitor dos PDFs do CLIB (PRIMAVERA BSS).

Só usa pdfminer.six (Python puro), para construir sem problemas em Alpine/aarch64.
Extrai por padrões de texto, depois de reconstruir as linhas da página pelas
coordenadas — nunca por posição fixa.

ler_pdf(bytes|caminho) -> dict:
  {tipo: 'factura'|'nota_credito'|'saldo'|'ignorar'|'desconhecido',
   numero, data (AAAA-MM-DD), valor (Decimal),
   categorias: [{categoria, valor}],   # só facturas/NC, agregado por categoria
   linhas: [{artigo, descricao, valor, categoria}],
   avisos: [str]}                      # não vazio => marcar "a rever"
"""
from __future__ import annotations

import io
import re
from collections import OrderedDict
from decimal import Decimal

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTTextContainer, LTTextLine

CATEGORIAS_POR_PREFIXO = {
    "M": "Mensalidade",
    "F": "Uniforme",
    "L": "Manuais Escolares",
    "P": "Material Escolar",
    "R": "Refeições",
    "I": "Inscrição/Matrícula",
    "C": "Actividades Extra Curriculares",
    "A": "Eventos",
}
CATEGORIA_DESCONHECIDA = "Outro"

VALOR = r"\d{1,3}(?:[ .]\d{3})*,\d{2}"
DATA_ISO = r"\b(\d{4}-\d{2}-\d{2})\b"


def valor_pt(txt: str) -> Decimal:
    """'5 000,00' / '1.630,00' / '983,13' -> Decimal."""
    return Decimal(txt.replace(" ", "").replace(".", "").replace(",", "."))


# ---------- Reconstrução das linhas ----------

def _chars(obj):
    if isinstance(obj, LTChar):
        yield obj
    elif hasattr(obj, "__iter__"):
        for o in obj:
            yield from _chars(o)


def linhas_pdf(fonte) -> list[str]:
    """Devolve as linhas de texto de todas as páginas, de cima para baixo,
    com colunas separadas por 3+ espaços."""
    if isinstance(fonte, (bytes, bytearray)):
        fonte = io.BytesIO(fonte)
    out: list[str] = []
    for pagina in extract_pages(fonte, laparams=LAParams()):
        chars = [c for c in _chars(pagina) if c.get_text().strip() or c.get_text() == " "]
        # agrupar por linha de base (tolerância de 2 pt)
        chars.sort(key=lambda c: (-round(c.y0, 1), c.x0))
        filas: list[list[LTChar]] = []
        for c in chars:
            if filas and abs(filas[-1][0].y0 - c.y0) <= 2.0:
                filas[-1].append(c)
            else:
                filas.append([c])
        for fila in filas:
            fila.sort(key=lambda c: c.x0)
            txt, ultimo = "", None
            for c in fila:
                t = c.get_text()
                if ultimo is not None:
                    gap = c.x0 - ultimo.x1
                    largura = max(ultimo.width, 1.0)
                    if gap > largura * 2.5:
                        txt += "   "
                    elif gap > largura * 0.25 and not txt.endswith(" ") and t != " ":
                        txt += " "
                txt += t
                ultimo = c
            txt = re.sub(r"[ ]{3,}", "   ", txt).strip()
            if txt:
                out.append(txt)
    return out


# ---------- Interpretação ----------

def _data_apos(linhas, cabecalho_re):
    for i, l in enumerate(linhas):
        if re.search(cabecalho_re, l):
            for seg in linhas[i + 1:i + 4]:
                m = re.search(DATA_ISO, seg)
                if m:
                    return m.group(1)
    return None


def ler_pdf(fonte) -> dict:
    linhas = linhas_pdf(fonte)
    texto = "\n".join(linhas)
    r = {"tipo": "desconhecido", "numero": None, "data": None, "valor": None,
         "categorias": [], "linhas": [], "avisos": []}

    if re.search(r"Extrato de conta de Cliente", texto):
        r["tipo"] = "ignorar"
        return r

    m_ft = re.search(r"Fatura\s+FT\s+(FA\.\d{4}A?/\d+)", texto)
    m_nc = re.search(r"Nota de Cr[eé]dito\s+NC\s+(NC\.\d{4}A?/\d+)", texto)
    m_adc = re.search(r"Adiantamento de Clientes\s+N[º°o]\s*(\d+/\d{4})", texto)
    m_re = re.search(r"Recebimento\s+(RE\s+\d{4}/\d+)", texto)

    if m_ft or m_nc:
        r["tipo"] = "factura" if m_ft else "nota_credito"
        r["numero"] = (m_ft or m_nc).group(1)
        r["data"] = _data_apos(linhas, r"^Data\b")
        m = re.search(r"Total\s*\(\s*EUR\s*\)\s+(" + VALOR + ")", texto)
        r["valor"] = valor_pt(m.group(1)) if m else None
        _ler_artigos(linhas, r)
    elif m_adc:
        r["tipo"] = "saldo"
        r["numero"] = m_adc.group(1)
        r["data"] = _data_apos(linhas, r"Data Doc\.")
        for i, l in enumerate(linhas):
            if "Valor Doc." in l:
                vs = re.findall(VALOR, " ".join(linhas[i + 1:i + 2]))
                if vs:
                    r["valor"] = valor_pt(vs[-1])
                break
    elif m_re:
        r["tipo"] = "saldo"
        r["numero"] = m_re.group(1)
        r["data"] = _data_apos(linhas, r"Data Doc\.")
        m = re.search(r"Total Recebido \(EUR\)\s+(" + VALOR + ")", texto)
        r["valor"] = valor_pt(m.group(1)) if m else None
    else:
        r["avisos"].append("Tipo de documento não reconhecido")
        return r

    for campo in ("numero", "data", "valor"):
        if r[campo] is None:
            r["avisos"].append(f"Campo não encontrado: {campo}")
    return r


# Códigos vistos: F0016, L1008, R71, P052, C151, A176 e também com espaço: "M 321", "I 101".
ARTIGO_RE = re.compile(r"^([A-Z]{1,3} ?\d{1,6})\s{3}(.*?)\s{3}.*?(" + VALOR + r")$")


def _ler_artigos(linhas, r):
    dentro = False
    for l in linhas:
        if l.startswith("Artigo") and "Valor" in l:
            dentro = True
            continue
        if not dentro:
            continue
        if re.search(r"A Transportar|Processado por Programa|Quadro Resumo", l):
            dentro = False
            continue
        m = ARTIGO_RE.match(l)
        if m:
            artigo, desc, val = m.group(1), m.group(2).strip(), valor_pt(m.group(3))
            cat = CATEGORIAS_POR_PREFIXO.get(re.match(r"[A-Z]+", artigo).group(0)[0])
            if cat is None:
                cat = CATEGORIA_DESCONHECIDA
                r["avisos"].append(f"Código de artigo desconhecido: {artigo} ({desc})")
            r["linhas"].append({"artigo": artigo, "descricao": desc, "valor": val, "categoria": cat})

    agreg: "OrderedDict[str, Decimal]" = OrderedDict()
    for ln in r["linhas"]:
        agreg[ln["categoria"]] = agreg.get(ln["categoria"], Decimal("0")) + ln["valor"]
    r["categorias"] = [{"categoria": c, "valor": v} for c, v in sorted(agreg.items())]

    soma = sum((ln["valor"] for ln in r["linhas"]), Decimal("0"))
    if not r["linhas"]:
        r["avisos"].append("Nenhuma linha de artigo encontrada")
    elif r["valor"] is not None and soma != r["valor"]:
        r["avisos"].append(f"Soma das linhas ({soma}) diferente do total ({r['valor']})")


def nome_ficheiro(r: dict) -> str | None:
    """Nome no formato do colégio: FA_2026A_6876.pdf, NC_2026_58.pdf, RE_2026_1918.pdf, ADC_90_2026.pdf."""
    n = r.get("numero")
    if not n:
        return None
    if r["tipo"] in ("factura", "nota_credito"):
        return n.replace(".", "_").replace("/", "_") + ".pdf"
    if n.startswith("RE "):
        return n.replace(" ", "_").replace("/", "_") + ".pdf"
    return "ADC_" + n.replace("/", "_") + ".pdf"
