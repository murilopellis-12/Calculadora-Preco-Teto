"""
Funções PURAS de normalização dos dados da CVM.

"Pura" = recebe um DataFrame, devolve outro, sem tocar em rede, disco ou banco.
Isso facilita testar (veja tests/test_normalize.py) e é o motivo de separarmos
estas funções do script de ingestão.

Quirks dos dados da CVM que este módulo trata:
  1. Valores vêm em escala (UNIDADE ou MIL) -> convertemos tudo para a unidade da moeda.
  2. Um mesmo documento pode ser reapresentado (campo VERSAO) -> ficamos com a mais recente.
  3. Texto vem com acento/caixa variados ("ÚLTIMO") -> comparamos sem acento.
  4. Demonstrações de fluxo (DRE, DFC, DVA) no ITR vêm ACUMULADAS no ano -> derivamos o trimestre isolado.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

# Fator multiplicador de cada valor possível de ESCALA_MOEDA.
ESCALAS = {
    "UNIDADE": 1.0,
    "MIL": 1_000.0,
    "MILHAO": 1_000_000.0,
    "MILHOES": 1_000_000.0,
}


def sem_acento(texto) -> str:
    """'ÚLTIMO' -> 'ULTIMO'. Valores nulos viram string vazia."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in decomposto if not unicodedata.combining(c)).upper().strip()


def so_digitos(valor) -> str:
    """'33.000.167/0001-01' -> '33000167000101'. Usamos o CNPJ só com dígitos como chave."""
    return re.sub(r"\D", "", str(valor))


def aplicar_escala(df: pd.DataFrame, col_valor: str = "vl_conta", col_escala: str = "escala_moeda") -> pd.DataFrame:
    """Converte o valor para a unidade da moeda (ex.: 'MIL' multiplica por 1000).

    Levanta ValueError se aparecer uma escala desconhecida: preferimos parar a
    ficar com valores errados em silêncio.
    """
    escala = df[col_escala].map(sem_acento)
    desconhecidas = set(escala.unique()) - set(ESCALAS)
    if desconhecidas:
        raise ValueError(f"Escala(s) desconhecida(s) em ESCALA_MOEDA: {sorted(desconhecidas)}")
    saida = df.copy()
    saida[col_valor] = pd.to_numeric(saida[col_valor], errors="coerce") * escala.map(ESCALAS)
    return saida


def manter_ultima_versao(df: pd.DataFrame, chaves: list[str], col_versao: str = "versao") -> pd.DataFrame:
    """Para cada grupo definido por `chaves`, mantém só as linhas da maior VERSAO."""
    saida = df.copy()
    saida[col_versao] = pd.to_numeric(saida[col_versao], errors="coerce").fillna(0).astype(int)
    maior = saida.groupby(chaves)[col_versao].transform("max")
    return saida[saida[col_versao] == maior].reset_index(drop=True)


def ytd_para_trimestral(
    df: pd.DataFrame,
    chaves: tuple[str, ...] = ("cnpj", "escopo", "demonstracao", "cd_conta"),
) -> pd.DataFrame:
    """Transforma valores ACUMULADOS no ano (YTD) em valores do TRIMESTRE isolado.

    Entrada: linhas de DRE/DFC/DVA vindas de ITR (1T, 2T, 3T) e DFP (anual), com
             colunas `chaves` + dt_ini_exerc, dt_fim_exerc, vl_conta.
    Lógica:  1T = acumulado do 1T
             2T = acumulado 6M  - acumulado 3M
             3T = acumulado 9M  - acumulado 6M
             4T = anual (DFP)   - acumulado 9M
    Se faltar um trimestre intermediário, o resultado daquele período fica NaN
    (melhor um buraco visível do que um número errado).

    Não serve para Balanço Patrimonial (BPA/BPP), que já é uma "foto" na data.
    Premissa: exercício social = ano-calendário (1º/jan a 31/dez), o caso da
    grande maioria das empresas da B3. Exercícios diferentes são ignorados.
    """
    chaves = list(chaves)
    d = df.copy()
    d["dt_ini_exerc"] = pd.to_datetime(d["dt_ini_exerc"])
    d["dt_fim_exerc"] = pd.to_datetime(d["dt_fim_exerc"])

    # Só as linhas acumuladas desde 1º de janeiro.
    ytd = d[(d["dt_ini_exerc"].dt.month == 1) & (d["dt_ini_exerc"].dt.day == 1)].copy()
    ytd["ano"] = ytd["dt_fim_exerc"].dt.year
    ytd["mes"] = ytd["dt_fim_exerc"].dt.month
    ytd = ytd.sort_values([*chaves, "ano", "mes"]).drop_duplicates([*chaves, "ano", "mes"], keep="last")

    grupo = ytd.groupby([*chaves, "ano"])
    mes_anterior = grupo["mes"].shift(1).fillna(0)
    valor_anterior = grupo["vl_conta"].shift(1).fillna(0.0)

    # O período só é um trimestre "limpo" se começou exatamente 3 meses depois do anterior.
    ytd["vl_trimestre"] = (ytd["vl_conta"] - valor_anterior).where((ytd["mes"] - mes_anterior) == 3)
    ytd["trimestre"] = ytd["mes"] // 3
    return ytd[[*chaves, "ano", "trimestre", "dt_fim_exerc", "vl_trimestre"]].reset_index(drop=True)
