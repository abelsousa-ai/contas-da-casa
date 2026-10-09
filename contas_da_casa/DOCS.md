# Contas da casa

## Primeira utilização
1. Abre "Contas da casa" no menu lateral.
2. Como ainda não há base de dados, aparece a página "Carregar a base de dados inicial".
   Escolhe o ficheiro `financas_inicial.sqlite` e carrega-o. Só é pedido uma vez.

## Onde ficam os dados
- Base de dados: `/data/financas.sqlite`, dentro da app. Entra nos backups do Home Assistant.
  A app pára durante o backup (`backup: cold`) para a cópia ficar consistente.
- Para teres uma cópia fora do Home Assistant, usa **Exportar** e guarda o Excel.
- Desinstalar a app apaga os dados. Faz um backup ou exporta antes.

## Acesso
- Só pelo painel do Home Assistant (ingress). A app recusa ligações que não venham do ingress.
- Qualquer utilizador do Home Assistant vê o painel (não só administradores).

## Importar extractos
- ActivoBank: os ficheiros Excel tal como saem do banco (conta à ordem e cartão de crédito).
- Revolut: o CSV do extracto da conta.
- Os movimentos já importados não entram outra vez. Tudo o que é anterior a 2026 é ignorado.
