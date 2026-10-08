# AGENTS.md — Analisador de Ações (uso pessoal)

Você está ajudando a construir um app pessoal de análise COMPLETA de ações da B3:
indicadores fundamentalistas, vários métodos de preço teto lado a lado, e varredura
recorrente dos dados abertos da CVM. O dono do projeto é estudante de desenvolvimento
de software e investidor de longo prazo; explique decisões de design em linguagem clara
e responda em português do Brasil. Comentários e nomes de domínio no código em português.

## Stack (decisão tomada, não mude sem perguntar)
- Python 3.11+, pandas, DuckDB (arquivo único em `data/acoes.duckdb`), requests, pytest.
- Interface (fase 2): Streamlit + Plotly. Cotações/proventos (fase 2): yfinance, com brapi como plano B.
- Sem servidor, sem autenticação, sem Docker: é uso pessoal.

## Estrutura
- `src/normalize.py`  funções PURAS de limpeza dos dados da CVM (escala, versão, YTD -> trimestre).
- `src/db.py`         conexão e esquema do DuckDB.
- `src/ingest_cvm.py` baixa DFP/ITR/FCA da CVM, filtra por tickers e grava no banco.
- `src/valuation.py`  um método de preço teto por função pura (Bazin, Graham, Gordon, DCF, projetivo).
- `tests/`            pytest. Toda função nova de cálculo nasce com teste.
- `tickers.txt`       lista de tickers acompanhados.

## Regras de engenharia
1. Separe I/O de cálculo. Funções de cálculo não tocam em rede, disco ou banco.
2. Cada método de preço teto devolve `PrecoTeto` (valor, premissas, observação). Se o método
   não se aplica, `valor=None` e a observação explica por quê. Nunca invente número.
3. Valores monetários ficam em unidade da moeda (não em milhares) depois da normalização.
4. Ingestão idempotente: rodar duas vezes não duplica linhas.
5. Antes de dizer que algo funciona, rode `python -m pytest -q`.
6. Não martele os servidores da CVM: use o cache em `data/raw/` e baixe de novo só com `--atualizar`.
7. Não instale dependências novas sem avisar e sem adicioná-las ao `requirements.txt`.

## Armadilhas dos dados da CVM (já tratadas ou a tratar)
- Escala: `ESCALA_MOEDA` pode ser MIL ou UNIDADE.
- Reapresentações: manter a maior `VERSAO` por empresa/data.
- Usar demonstrações consolidadas (`con`) por padrão; `ind` só quando pedido.
- ITR: DRE/DFC/DVA vêm acumuladas no ano. Use `ytd_para_trimestral` para obter o trimestre isolado.
  Q4 = anual (DFP) menos acumulado de 9 meses. Balanço (BPA/BPP) é foto e não precisa disso.
- Bancos e seguradoras têm plano de contas diferente: Graham e DCF tradicional não se aplicam
  igual a eles. Tratar financeiras à parte (DDM/Gordon, ROE, P/VP).
- Ações ON/PN/Units da mesma empresa compartilham o mesmo CNPJ: lucro por ação deve considerar
  o total de ações (tabela `composicao_capital`, descontando tesouraria).
- Algumas empresas reportam em outra moeda (coluna `moeda`): sinalizar, não misturar.
- Os nomes de colunas dos CSVs reais da CVM ainda NÃO foram conferidos contra arquivos baixados;
  o código foi escrito de forma defensiva e os testes usam zips sintéticos. Na primeira execução
  real, confira com `--resumo` e ajuste `src/ingest_cvm.py` se algum nome de coluna diferir.

## Escopo e limites
- O app é uma ferramenta de apoio à análise. Não gere recomendações de compra/venda; mostre
  números, premissas e margem de segurança.
- O método "preço teto projetivo" (dividendo projetado / yield mínimo, padrão 8%) está só em versão
  mínima. PERGUNTE ao dono como o dividendo deve ser projetado antes de implementar de verdade.

## Roadmap (peça confirmação a cada fase)
1. [feito] Ingestão CVM -> DuckDB com testes.
2. Conferir a ingestão com dados reais de 3 a 5 tickers; corrigir colunas se preciso.
3. Camada de métricas: LPA, VPA, ROE, ROIC, margens, dívida líquida/EBITDA, payout, CAGR.
   Contas-chave por `cd_conta` (ex.: 3.01 receita, 3.11 lucro líquido), mapeadas em um dicionário único.
4. Cotações e proventos (yfinance), ajustados por splits/bonificações.
5. Motor de preço teto completo com faixa de valores e margem de segurança por método.
6. App Streamlit: tela do ativo (indicadores, histórico, métodos lado a lado) e screener.
7. Varredura recorrente (cron/Agendador) + alerta "preço abaixo do teto".

## Como rodar
```
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q
python -m src.ingest_cvm --anos 2023 2024 2025
python -m src.ingest_cvm --resumo
```
