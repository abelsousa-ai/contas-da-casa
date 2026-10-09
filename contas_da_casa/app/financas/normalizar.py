"""Normalização de descrições (comum à migração e à aplicação)."""
import re

def fix_mojibake(s):
    """Corrige texto UTF-8 lido como Latin-1 (ex.: 'numerÃ¡rio' -> 'numerário')."""
    if not isinstance(s, str) or 'Ã' not in s:
        return s
    try:
        return s.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s

def norm_desc(s):
    """Descrição sem dígitos do cartão, referências numéricas e 'CONTACTLESS'."""
    s = str(s).upper()
    s = re.sub(r'^COMPRA \d{4} ', '', s)
    s = s.replace('CONTACTLESS', '')
    s = re.sub(r'\b[A-Z]{0,4}\d[\w./-]*', '', s)
    return re.sub(r'\s+', ' ', s).strip()
