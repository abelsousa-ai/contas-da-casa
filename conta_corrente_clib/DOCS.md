# Conta Corrente CLIB

## Como funciona
1. **Sincronizar** chama a ponte Google (Apps Script, na conta do Abel), que procura os emails de
   `billing@clib.pt` com PDF, guarda os PDFs na pasta do Drive `CLIB - Documentos` e devolve-os.
2. O add-on lê cada PDF (Fatura, Nota de Crédito, Adiantamento, Recebimento), insere o movimento e a
   repartição por categoria a partir das linhas de artigo. Extractos de conta são ignorados.
3. Se a soma das linhas não bater com o total, ou aparecer um código de artigo desconhecido,
   o movimento entra marcado **a rever**.

Duplicados são impossíveis: cada documento é único por (tipo, número).

## Configuração
Definições → URL da aplicação Web do Apps Script (termina em `/exec`) e o token.
São guardados em `/data`, nunca no repositório. "Guardar e testar" só grava se a ligação funcionar.

## Regras
- Saldo = Saldos + Notas de crédito − Facturas. Alerta abaixo de 1.000 €.
- Consumo = Facturas − Notas de crédito.
- Ano escolar: Setembro a Julho (Agosto conta para o ano seguinte). Excepção: 2026/2027 = Abril 2026 a Julho 2027.
- Códigos de artigo: M Mensalidade, F Uniforme, L Manuais, P Material, R Refeições, I Inscrição/Matrícula,
  C Actividades Extra Curriculares, A Eventos. Outros → "Outro" + a rever.

## Dados
- Base de dados: `/data/clib.sqlite`; PDFs: `/data/pdfs`. Entram nos backups do Home Assistant.
- Se tudo se perder: instalar de novo, configurar a ponte e "Reprocessar todos os emails" reconstrói a conta
  (a FA.2026A/2922 tem de ser carregada à mão — não veio para este email).
- Desinstalar o add-on apaga `/data`.

## Segurança
- Só aceita ligações do gateway do ingress (172.30.32.2), com o login do Home Assistant.
- O token da ponte protege o acesso ao Gmail/Drive; se for exposto, correr `novoToken` no Apps Script
  e actualizar nas Definições.
