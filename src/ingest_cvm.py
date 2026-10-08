"""
Ingestão dos dados abertos da CVM para um banco DuckDB local.

O que este script faz, em ordem:
  1. Lê a lista de tickers (tickers.txt) que você quer acompanhar.
  2. Descobre o CNPJ de cada ticker usando o FCA da CVM (ticker -> CNPJ).
  3. Baixa os zips anuais de DFP (demonstrações anuais) e ITR (trimestrais).
  4. Filtra só as empresas escolhidas, normaliza (escala, versão, acentos) e
     grava no DuckDB (data/acoes.duckdb).

Uso rápido (na raiz do projeto):
    python -m src.ingest_cvm                      # últimos 6 anos, DFP + ITR, tickers.txt
    python -m src.ingest_cvm --anos 2023 2024     # só alguns anos
    python -m src.ingest_cvm --todas              # TODAS as empresas (sem filtro de ticker)
    python -m src.ingest_cvm --resumo             # mostra o que há no banco
    python -m src.ingest_cvm --atualizar          # força baixar de novo os zips

É idempotente: rodar duas vezes não duplica dados (apaga o recorte e reinsere).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from src.db import conectar
from src.normalize import aplicar_escala, manter_ultima_versao, sem_acento, so_digitos

log = logging.getLogger("ingest_cvm")

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
URLS = {
    "DFP": BASE + "/DFP/DADOS/dfp_cia_aberta_{ano}.zip",
    "ITR": BASE + "/ITR/DADOS/itr_cia_aberta_{ano}.zip",
    "FCA": BASE + "/FCA/DADOS/fca_cia_aberta_{ano}.zip",
}

# Ex.: dfp_cia_aberta_DRE_con_2024.csv  /  itr_cia_aberta_BPA_ind_2025.csv
PADRAO_ARQUIVO = re.compile(
    r"^(?P<fonte>dfp|itr)_cia_aberta_(?P<demo>BPA|BPP|DRE|DFC_MD|DFC_MI|DVA)_(?P<escopo>con|ind)_(?P<ano>\d{4})\.csv$",
    re.IGNORECASE,
)
PADRAO_TICKER = re.compile(r"^[A-Z]{4}\d{1,2}$")  # PETR4, VALE3, TAEE11...

COLUNAS_DEMO = [
    "cnpj", "cd_cvm", "nome", "fonte", "ano_arquivo", "demonstracao", "escopo", "versao", "moeda",
    "dt_refer", "dt_ini_exerc", "dt_fim_exerc", "cd_conta", "ds_conta", "vl_conta",
]
COLUNAS_CAPITAL = [
    "cnpj", "fonte", "ano_arquivo", "dt_refer", "versao",
    "qt_ordinarias", "qt_preferenciais", "qt_total",
    "qt_tesouro_ordinarias", "qt_tesouro_preferenciais", "qt_tesouro_total",
]
# Coluna da CVM -> coluna nossa (composição do capital)
MAPA_CAPITAL = {
    "QT_ACAO_ORDIN_CAP_INTEGR": "qt_ordinarias",
    "QT_ACAO_PREF_CAP_INTEGR": "qt_preferenciais",
    "QT_ACAO_TOTAL_CAP_INTEGR": "qt_total",
    "QT_ACAO_ORDIN_TESOURO": "qt_tesouro_ordinarias",
    "QT_ACAO_PREF_TESOURO": "qt_tesouro_preferenciais",
    "QT_ACAO_TOTAL_TESOURO": "qt_tesouro_total",
}


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def baixar(url: str, destino: Path, atualizar: bool = False) -> Path | None:
    """Baixa `url` para `destino`. Devolve o caminho, ou None se não existir (404).

    Se o arquivo já existe e `atualizar` é False, reaproveita o que está em disco.
    """
    if destino.exists() and not atualizar:
        log.info("Reaproveitando %s", destino.name)
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    for tentativa in range(1, 4):
        try:
            log.info("Baixando %s (tentativa %d)", url, tentativa)
            resp = requests.get(url, stream=True, timeout=60, headers={"User-Agent": "stock-analyzer/0.1"})
            if resp.status_code == 404:
                log.warning("Não encontrado (404): %s", url)
                return destino if destino.exists() else None
            resp.raise_for_status()
            parcial = destino.with_suffix(destino.suffix + ".part")
            with open(parcial, "wb") as f:
                for bloco in resp.iter_content(chunk_size=1 << 20):
                    f.write(bloco)
            parcial.replace(destino)
            return destino
        except requests.RequestException as erro:
            log.warning("Falha no download: %s", erro)
            time.sleep(2 ** tentativa)
    return destino if destino.exists() else None


def ler_csv_zip(zf: zipfile.ZipFile, nome: str) -> pd.DataFrame:
    """Lê um CSV de dentro do zip. Os arquivos da CVM usam ';' e encoding latin-1."""
    with zf.open(nome) as arquivo:
        df = pd.read_csv(arquivo, sep=";", encoding="latin-1", dtype=str)
    df.columns = [c.strip().upper() for c in df.columns]
    return df


# --------------------------------------------------------------------------- #
# Ticker -> CNPJ (via FCA)
# --------------------------------------------------------------------------- #
def extrair_mapa_fca(zip_path: Path) -> pd.DataFrame:
    """Extrai (ticker, cnpj) do arquivo 'valor_mobiliario' do FCA."""
    with zipfile.ZipFile(zip_path) as zf:
        candidatos = [n for n in zf.namelist() if "valor_mobiliario" in n.lower()]
        if not candidatos:
            raise ValueError(f"Nenhum arquivo 'valor_mobiliario' em {zip_path.name}: {zf.namelist()}")
        df = ler_csv_zip(zf, candidatos[0])

    col_cnpj = next((c for c in df.columns if "CNPJ" in c), None)
    col_ticker = next((c for c in df.columns if "CODIGO_NEGOCIACAO" in sem_acento(c)), None)
    if not col_cnpj or not col_ticker:
        raise ValueError(
            "Não achei as colunas de CNPJ/ticker no FCA. Colunas disponíveis: "
            f"{list(df.columns)}. Ajuste extrair_mapa_fca() ou use --mapa-manual."
        )

    linhas = []
    for cnpj, bruto in zip(df[col_cnpj], df[col_ticker]):
        if pd.isna(bruto):
            continue
        for ticker in re.split(r"[;,\s]+", str(bruto).upper()):
            if PADRAO_TICKER.match(ticker):
                linhas.append((ticker, so_digitos(cnpj)))
    return pd.DataFrame(linhas, columns=["ticker", "cnpj"]).drop_duplicates()


def montar_mapa_tickers(raw_dir: Path, ano_ref: int, atualizar: bool, mapa_manual: Path | None) -> pd.DataFrame:
    """Junta o mapa ticker->CNPJ dos FCAs dos últimos anos (o mais recente tem prioridade)."""
    partes = []
    if mapa_manual:
        manual = pd.read_csv(mapa_manual, dtype=str)
        manual.columns = [c.strip().lower() for c in manual.columns]
        manual["ticker"] = manual["ticker"].str.upper().str.strip()
        manual["cnpj"] = manual["cnpj"].map(so_digitos)
        partes.append(manual[["ticker", "cnpj"]])

    for ano in (ano_ref, ano_ref - 1, ano_ref - 2):
        zp = baixar(URLS["FCA"].format(ano=ano), raw_dir / "FCA" / f"fca_cia_aberta_{ano}.zip",
                    atualizar and ano == ano_ref)
        if zp:
            partes.append(extrair_mapa_fca(zp))

    if not partes:
        raise RuntimeError("Não consegui montar o mapa ticker->CNPJ (FCA indisponível). Use --mapa-manual.")
    mapa = pd.concat(partes, ignore_index=True)
    # Se um ticker aparece com CNPJs diferentes (reaproveitado), vale o primeiro = mais recente.
    return mapa.drop_duplicates("ticker", keep="first").reset_index(drop=True)


def ler_tickers(caminho: Path) -> list[str]:
    tickers = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#")[0].strip().upper()
        if linha:
            tickers.append(linha)
    return tickers


# --------------------------------------------------------------------------- #
# Processamento dos zips de DFP/ITR
# --------------------------------------------------------------------------- #
def processar_demonstracoes(
    zip_path: Path, fonte: str, ano: int, cnpjs: set[str] | None, escopos: set[str], demos: set[str] | None = None,
) -> pd.DataFrame:
    """Lê as demonstrações (BP, DRE, DFC, DVA) de um zip e devolve um DataFrame normalizado."""
    pedacos = []
    with zipfile.ZipFile(zip_path) as zf:
        for nome in zf.namelist():
            m = PADRAO_ARQUIVO.match(nome)
            if not m:
                continue
            demo, escopo = m["demo"].upper(), m["escopo"].lower()
            if escopo not in escopos or (demos and demo not in demos):
                continue

            df = ler_csv_zip(zf, nome)
            df["CNPJ"] = df["CNPJ_CIA"].map(so_digitos)
            if cnpjs is not None:
                df = df[df["CNPJ"].isin(cnpjs)]
            # Só o exercício atual (ignora a coluna comparativa 'PENÚLTIMO').
            df = df[df["ORDEM_EXERC"].map(sem_acento) == "ULTIMO"]
            if df.empty:
                continue

            if "DT_INI_EXERC" not in df:  # balanço não tem data inicial
                df["DT_INI_EXERC"] = None
            if "DT_FIM_EXERC" not in df:
                df["DT_FIM_EXERC"] = df["DT_REFER"]

            pedaco = pd.DataFrame({
                "cnpj": df["CNPJ"], "cd_cvm": df.get("CD_CVM"), "nome": df.get("DENOM_CIA"),
                "fonte": fonte, "ano_arquivo": ano, "demonstracao": demo, "escopo": escopo,
                "versao": df["VERSAO"], "moeda": df.get("MOEDA"), "escala_moeda": df["ESCALA_MOEDA"],
                "dt_refer": pd.to_datetime(df["DT_REFER"]), "dt_ini_exerc": pd.to_datetime(df["DT_INI_EXERC"]),
                "dt_fim_exerc": pd.to_datetime(df["DT_FIM_EXERC"]), "cd_conta": df["CD_CONTA"],
                "ds_conta": df["DS_CONTA"], "vl_conta": df["VL_CONTA"],
            })
            pedacos.append(pedaco)

    if not pedacos:
        return pd.DataFrame(columns=COLUNAS_DEMO)

    todos = pd.concat(pedacos, ignore_index=True)
    todos = aplicar_escala(todos)
    # Reapresentações: fica só a maior versão de cada (empresa, demonstração, data).
    todos = manter_ultima_versao(todos, ["cnpj", "demonstracao", "escopo", "dt_refer"])
    return todos[COLUNAS_DEMO]


def processar_capital(zip_path: Path, fonte: str, ano: int, cnpjs: set[str] | None) -> pd.DataFrame:
    """Lê a composição do capital (quantidade de ações) de um zip de DFP/ITR."""
    with zipfile.ZipFile(zip_path) as zf:
        candidatos = [n for n in zf.namelist() if "composicao_capital" in n.lower()]
        if not candidatos:
            return pd.DataFrame(columns=COLUNAS_CAPITAL)
        df = ler_csv_zip(zf, candidatos[0])

    df["CNPJ"] = df["CNPJ_CIA"].map(so_digitos)
    if cnpjs is not None:
        df = df[df["CNPJ"].isin(cnpjs)]
    if df.empty:
        return pd.DataFrame(columns=COLUNAS_CAPITAL)

    saida = pd.DataFrame({
        "cnpj": df["CNPJ"], "fonte": fonte, "ano_arquivo": ano,
        "dt_refer": pd.to_datetime(df["DT_REFER"]), "versao": df["VERSAO"],
    })
    for coluna_cvm, coluna in MAPA_CAPITAL.items():
        saida[coluna] = pd.to_numeric(df[coluna_cvm], errors="coerce") if coluna_cvm in df else float("nan")
    saida = manter_ultima_versao(saida, ["cnpj", "dt_refer"])
    return saida[COLUNAS_CAPITAL]


# --------------------------------------------------------------------------- #
# Gravação no DuckDB
# --------------------------------------------------------------------------- #
def gravar_demonstracoes(con, df: pd.DataFrame) -> int:
    """Apaga o recorte (fonte, ano, empresa, escopo) e reinsere. Idempotente."""
    if df.empty:
        return 0
    con.register("df_tmp", df)
    con.execute(
        "DELETE FROM demonstracoes WHERE (fonte, ano_arquivo, cnpj, escopo) IN "
        "(SELECT DISTINCT fonte, ano_arquivo, cnpj, escopo FROM df_tmp)"
    )
    cols = ", ".join(COLUNAS_DEMO)
    con.execute(f"INSERT INTO demonstracoes ({cols}) SELECT {cols} FROM df_tmp")
    con.unregister("df_tmp")
    return len(df)


def gravar_capital(con, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    con.register("df_tmp", df)
    con.execute(
        "DELETE FROM composicao_capital WHERE (fonte, ano_arquivo, cnpj) IN "
        "(SELECT DISTINCT fonte, ano_arquivo, cnpj FROM df_tmp)"
    )
    cols = ", ".join(COLUNAS_CAPITAL)
    con.execute(f"INSERT INTO composicao_capital ({cols}) SELECT {cols} FROM df_tmp")
    con.unregister("df_tmp")
    return len(df)


def gravar_empresas_e_tickers(con, demos: pd.DataFrame, mapa: pd.DataFrame) -> None:
    if not demos.empty:
        empresas = demos[["cnpj", "cd_cvm", "nome"]].drop_duplicates("cnpj")
        con.register("emp_tmp", empresas)
        con.execute("DELETE FROM empresas WHERE cnpj IN (SELECT cnpj FROM emp_tmp)")
        con.execute("INSERT INTO empresas SELECT cnpj, cd_cvm, nome FROM emp_tmp")
        con.unregister("emp_tmp")
        mapa = mapa[mapa["cnpj"].isin(empresas["cnpj"])]
    if not mapa.empty:
        con.register("tk_tmp", mapa)
        con.execute("DELETE FROM tickers WHERE ticker IN (SELECT ticker FROM tk_tmp)")
        con.execute("INSERT INTO tickers SELECT ticker, cnpj FROM tk_tmp")
        con.unregister("tk_tmp")


def mostrar_resumo(con) -> None:
    """Imprime o que existe no banco (bom para conferir se a ingestão funcionou)."""
    df = con.execute("""
        SELECT e.nome, d.fonte, d.escopo, min(d.dt_refer) AS de, max(d.dt_refer) AS ate, count(*) AS linhas
        FROM demonstracoes d JOIN empresas e ON e.cnpj = d.cnpj
        GROUP BY e.nome, d.fonte, d.escopo
        ORDER BY e.nome, d.fonte, d.escopo
    """).df()
    print(df.to_string(index=False) if not df.empty else "Banco vazio. Rode a ingestão primeiro.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ano_atual = date.today().year
    p = argparse.ArgumentParser(description="Ingestão de DFP/ITR da CVM para DuckDB")
    p.add_argument("--anos", nargs="+", type=int, default=list(range(ano_atual - 5, ano_atual + 1)))
    p.add_argument("--fontes", nargs="+", choices=["DFP", "ITR"], default=["DFP", "ITR"])
    p.add_argument("--escopos", nargs="+", choices=["con", "ind"], default=["con"],
                   help="con = consolidado (padrão), ind = individual")
    p.add_argument("--tickers", default="tickers.txt")
    p.add_argument("--todas", action="store_true", help="ignora tickers.txt e carrega todas as empresas")
    p.add_argument("--db", default="data/acoes.duckdb")
    p.add_argument("--raw", default="data/raw/cvm", help="pasta onde os zips baixados ficam em cache")
    p.add_argument("--mapa-manual", default=None, help="CSV com colunas ticker,cnpj (complementa o FCA)")
    p.add_argument("--atualizar", action="store_true", help="baixa os zips de novo")
    p.add_argument("--resumo", action="store_true", help="só mostra o conteúdo do banco")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    con = conectar(args.db)
    if args.resumo:
        mostrar_resumo(con)
        return 0

    raw = Path(args.raw)
    mapa = montar_mapa_tickers(raw, max(args.anos), args.atualizar, Path(args.mapa_manual) if args.mapa_manual else None)

    cnpjs: set[str] | None = None
    if not args.todas:
        pedidos = ler_tickers(Path(args.tickers))
        if not pedidos:
            log.error("%s está vazio. Adicione tickers ou use --todas.", args.tickers)
            return 1
        achados = mapa[mapa["ticker"].isin(pedidos)]
        faltando = sorted(set(pedidos) - set(achados["ticker"]))
        if faltando:
            log.warning("Tickers sem CNPJ no FCA (ignorados; use --mapa-manual): %s", faltando)
        cnpjs = set(achados["cnpj"])
        if not cnpjs:
            log.error("Nenhum ticker pôde ser mapeado para CNPJ.")
            return 1
        log.info("Empresas selecionadas: %d", len(cnpjs))

    todas_demos = []
    for fonte in args.fontes:
        for ano in sorted(args.anos):
            # O zip do ano corrente muda ao longo do ano, então sempre rebaixamos.
            zp = baixar(URLS[fonte].format(ano=ano), raw / fonte / f"{fonte.lower()}_cia_aberta_{ano}.zip",
                        args.atualizar or ano == ano_atual)
            if not zp:
                log.warning("%s %d indisponível, pulando.", fonte, ano)
                continue
            demos = processar_demonstracoes(zp, fonte, ano, cnpjs, set(args.escopos))
            n1 = gravar_demonstracoes(con, demos)
            n2 = gravar_capital(con, processar_capital(zp, fonte, ano, cnpjs))
            log.info("%s %d: %d linhas de demonstrações, %d de composição do capital", fonte, ano, n1, n2)
            todas_demos.append(demos)

    if todas_demos:
        gravar_empresas_e_tickers(con, pd.concat(todas_demos, ignore_index=True), mapa)
    log.info("Pronto. Veja o resultado com: python -m src.ingest_cvm --resumo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
