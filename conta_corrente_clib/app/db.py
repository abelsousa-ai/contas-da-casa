"""Base de dados SQLite em /data (entra nos backups do Home Assistant).

Valores guardados em cêntimos (inteiros) para não haver erros de arredondamento.
"""
import os
import sqlite3

DB = os.environ.get("APP_DB", "/data/clib.sqlite")
PASTA_PDFS = os.environ.get("APP_PDFS", os.path.join(os.path.dirname(DB), "pdfs"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS movimentos (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo          TEXT NOT NULL CHECK (tipo IN ('saldo','factura','nota_credito')),
  data          TEXT NOT NULL,
  valor_cent    INTEGER NOT NULL CHECK (valor_cent > 0),
  numero        TEXT NOT NULL,
  origem        TEXT NOT NULL CHECK (origem IN ('email','manual')),
  email_id      TEXT,
  pdf_nome      TEXT,
  drive_file_id TEXT,
  drive_url     TEXT,
  a_rever       INTEGER NOT NULL DEFAULT 0,
  avisos        TEXT,
  criado_em     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (tipo, numero)
);
CREATE TABLE IF NOT EXISTS movimento_categorias (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  movimento_id  INTEGER NOT NULL REFERENCES movimentos(id) ON DELETE CASCADE,
  categoria     TEXT NOT NULL,
  valor_cent    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cat_mov ON movimento_categorias(movimento_id);
CREATE TABLE IF NOT EXISTS emails_processados (
  message_id    TEXT PRIMARY KEY,
  data_email    TEXT,
  resultado     TEXT,
  processado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS definicoes (
  chave TEXT PRIMARY KEY,
  valor TEXT
);
"""


def ligar() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def iniciar() -> None:
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    os.makedirs(PASTA_PDFS, exist_ok=True)
    con = ligar()
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(ESQUEMA)
        con.commit()
    finally:
        con.close()


def obter_def(con, chave, omissao=None):
    r = con.execute("SELECT valor FROM definicoes WHERE chave = ?", (chave,)).fetchone()
    return r["valor"] if r and r["valor"] is not None else omissao


def guardar_def(con, chave, valor):
    con.execute("INSERT INTO definicoes(chave, valor) VALUES (?, ?) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, valor))
