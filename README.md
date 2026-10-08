# Analisador de Ações

App pessoal para análise **completa** de ações da B3: indicadores fundamentalistas, vários métodos de
**preço teto** lado a lado (Bazin, Graham, Gordon, DCF, projetivo...) e varredura recorrente dos dados
abertos da CVM.

Este repositório é o **ponto de partida (fase 1)**: a ingestão dos dados da CVM para um banco local, o
motor de preço teto e os testes. Dá para abrir no Antigravity e continuar daqui com o agente.

---

## Como a coisa funciona

```
CVM (zips DFP/ITR/FCA)
      │  src/ingest_cvm.py   baixa, filtra pelos seus tickers, normaliza
      ▼
data/acoes.duckdb            um arquivo; tabelas: empresas, tickers,
      │                      demonstracoes, composicao_capital
      ▼
(fase 3) métricas            LPA, VPA, ROE, ROIC, margens, payout...
      ▼
src/valuation.py             um método de preço teto por função pura
      ▼
(fase 6) app Streamlit       tela do ativo + screener + alertas
```

| Arquivo | Para que serve |
|---|---|
| `src/ingest_cvm.py` | Baixa DFP (anual), ITR (trimestral) e FCA (ticker→CNPJ) e grava no DuckDB. Idempotente. |
| `src/normalize.py` | Corrige as manias dos dados da CVM: escala MIL/UNIDADE, reapresentações (VERSAO), acumulado no ano → trimestre. |
| `src/db.py` | Esquema e conexão do DuckDB. |
| `src/valuation.py` | Bazin, projetivo, Graham, Gordon e DCF como funções puras que devolvem premissas + observação. |
| `tests/` | 14 testes (pytest). A ingestão é testada com zips sintéticos no formato da CVM. |
| `tickers.txt` | As ações que você acompanha. **Troque pela sua lista.** |
| `AGENTS.md` | Instruções permanentes para o agente do Antigravity (stack, regras, armadilhas, roadmap). |

---

## Passo a passo no Antigravity

### 1. Abrir o projeto
Descompacte o zip e abra a pasta `stock-analyzer` como workspace (menu **File → Open Folder**).

### 2. Conferir as regras do agente
O projeto traz um `AGENTS.md` na raiz e uma regra em `.agents/rules/projeto.md` apontando para ele.
Abra o painel **Rules** do Antigravity e confirme que a regra do workspace aparece. Se não aparecer,
use **+ Workspace** e cole o conteúdo do `AGENTS.md` (cada arquivo de regra aceita até 12.000 caracteres;
o nosso tem cerca de 4.500).

### 3. Criar o ambiente e rodar os testes
No terminal integrado, na raiz do projeto:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                # deve mostrar: 14 passed
```

### 4. Primeira ingestão (comece pequeno!)
Edite o `tickers.txt` com 3 a 5 ações e rode só alguns anos:

```bash
python -m src.ingest_cvm --anos 2023 2024 2025
python -m src.ingest_cvm --resumo      # confere o que entrou no banco
```

Na primeira vez ele baixa os zips (ficam em cache em `data/raw/cvm/`). Reexecutar não duplica nada.
Quando estiver tudo certo, rode sem `--anos` (últimos 6 anos) ou com `--todas` para a B3 inteira.

### 5. Consultar os dados
```bash
python -c "import duckdb; print(duckdb.connect('data/acoes.duckdb').sql(\"\"\"
  SELECT e.nome, d.dt_fim_exerc, d.vl_conta
  FROM demonstracoes d JOIN empresas e USING (cnpj)
  WHERE d.demonstracao='DRE' AND d.cd_conta='3.11' AND d.fonte='DFP'
  ORDER BY e.nome, d.dt_fim_exerc\"\"\").df())"
```
(`3.11` é o lucro/prejuízo líquido na DRE padronizada.)

### 6. Pôr o agente para trabalhar
Abra o **Agent Manager** (ou o chat do agente) e cole este primeiro pedido:

> Leia o `AGENTS.md` e o `README.md`. Rode `python -m pytest -q` e `python -m src.ingest_cvm --anos 2024`
> com os tickers do `tickers.txt`. Depois use `--resumo` e me diga: (1) se as colunas dos CSVs reais da CVM
> bateram com o que o código espera, corrigindo o que for preciso, e (2) quais contas (`cd_conta`) existem
> na DRE e no balanço dessas empresas. Não implemente mais nada antes de eu confirmar.

Os próximos pedidos seguem o roadmap do `AGENTS.md`, uma fase de cada vez.

---

## Atenção: o que ainda não foi validado

- Os nomes de colunas e arquivos dos CSVs da CVM foram escritos de acordo com o formato conhecido dos
  dados abertos, mas **não foram conferidos contra arquivos reais baixados** (o ambiente onde o código
  foi gerado não alcança o site da CVM). Os testes usam zips sintéticos com esse formato. Se algo
  divergir na primeira execução, o erro costuma listar as colunas encontradas; é um ajuste pequeno em
  `src/ingest_cvm.py`, e é exatamente o primeiro pedido para o agente acima.
- O método **projetivo** está em versão mínima (dividendo projetado ÷ yield mínimo de 8%). Como projetar o
  dividendo é uma decisão sua, o agente foi instruído a perguntar antes de implementar.
- **Bancos e seguradoras** precisam de tratamento próprio (Graham e DCF tradicional não valem igual).
- Cotações e proventos ainda não entram: são a fase 4 (yfinance, com brapi como plano B).

## Variações úteis

- `--escopos con ind`: carrega também as demonstrações individuais.
- `--mapa-manual meu_mapa.csv`: CSV `ticker,cnpj` para tickers que o FCA não encontrar.
- `--atualizar`: força baixar os zips de novo (o ano corrente já é sempre rebaixado).

*Ferramenta de apoio à análise; não é recomendação de investimento.*
