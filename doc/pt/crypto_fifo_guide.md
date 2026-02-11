# Guia Completo — Importação, FIFO e Geração de Relatório IRS

---

## Visão geral

O sistema importa transações de múltiplas exchanges para uma base de dados SQLite (`data/crypto_fifo.db`), calcula o FIFO global e gera relatórios para a declaração de IRS em Portugal. Todos os valores estão em EUR.

**Exchanges suportadas**: Mt.Gox, Bitstamp, TRT (TheRockTrading), Bitfinex, Kraken, Binance, Binance Card, Coinbase, Coinbase Prime, Wirex, Revolut, GDTRE, Coinpal.

**Objetivo**: provar a origem das vendas via FIFO, separando as mais-valias isentas (detenção ≥365 dias, Anexo G1) das tributáveis (<365 dias, Anexo J).

---

## Estratégia: Importação por CSV (não por API)

Porquê CSV e não API:
- Sem limites de taxa ou problemas de paginação
- Dados históricos completos num único ficheiro
- Funciona com exchanges extintas (Mt.Gox)
- Mais rápido para grandes volumes
- Controlo total sobre a validação dos dados

---

## Estrutura dos ficheiros CSV

Cada exchange tem o seu ficheiro em `data/`:

```
data/
├── eurusd.csv                  ← Taxas BCE (atualizar antes de cada importação)
├── binance_trade_history_all.csv
├── coinbaseprime_orders.csv
├── coinbase_history.csv
├── bitstamp_history.csv
├── bitfinex_trades.csv
├── kraken_ledgers.csv
├── mtgox.csv
├── trt.csv
├── wirex_YYYY.csv              ← Um ficheiro por período
├── revolut_crypto.csv
├── binance_card.csv
├── coinpal.csv
├── gdtre.csv
└── heranca_YYYY.csv            ← Transações manuais/OTC
```

**Regra**: um ficheiro CSV por exchange com **o histórico completo** (não ficheiros separados por ano). Quando chegam novos dados, acrescentá-los ao ficheiro existente.

---

## Fase 1 — Preparação

### Verificar o CSV antes de importar

```python
import pandas as pd
df = pd.read_csv('data/bitstamp_history.csv', nrows=5)
print(df.columns.tolist())
print(df.head())
```

Verificar:
1. Formato da data
2. Nomes das colunas (cada exchange é diferente)
3. Moeda (EUR ou USD?)
4. Codificação (UTF-8, não UTF-16 ou ISO-8859)

### Atualizar taxas BCE

O ficheiro `data/eurusd.csv` contém as taxas de câmbio históricas EUR/USD do BCE. Deve ser atualizado antes de importar exchanges que operam em USD (Coinbase Prime, Bitstamp, Bitfinex).

Se o ficheiro não estiver atualizado, o `ecb_rates.py` usará silenciosamente a última taxa disponível — mas apresentará um aviso:

```
⚠️  Taxa BCE para 2026-01-15: taxa mais próxima é 2025-12-31 (-15d) — ATUALIZE eurusd.csv!
```

---

## Fase 2 — Importação por exchange

Cada importador está em `importers/` e segue o mesmo padrão:

1. Pergunta se deve apagar os dados existentes dessa exchange (**DELETE + reimportar**)
2. Lê o CSV com o mapeamento de colunas específico da exchange
3. Converte para EUR se necessário (via `ecb_rates.py`)
4. Insere na tabela `transactions`

### Ordem recomendada

Importar primeiro as exchanges históricas (compras mais antigas), depois as recentes:

```bash
# Exchanges históricas
python3 importers/import_mtgox_with_fees.py
python3 importers/import_bitstamp_with_fees.py
python3 importers/import_trt_with_fees.py
python3 importers/import_bitfinex_ecb.py
python3 importers/import_kraken_with_fees.py

# Exchanges recentes
python3 importers/import_coinbase_standalone.py
python3 importers/import_coinbase_prime.py
python3 importers/import_binance_with_fees.py
python3 importers/import_binance_card.py
python3 importers/import_wirex.py
python3 importers/import_revolut.py

# Transações manuais/OTC
python3 importers/import_standard_csv.py data/heranca_YYYY.csv "Herança"
python3 importers/import_standard_csv.py data/otc_YYYY.csv "OTC"
```

### Verificar após cada importação

```bash
python3 importers/verify_exchange_import.py "Binance"
python3 importers/verify_exchange_import.py "Coinbase Prime"
```

O script compara totais de BUY/SELL, quantidades e comissões.

### Tratamento de comissões

Regra crítica na base de dados:
- `total_value` = valor bruto (ANTES de comissões)
- `fee_amount` = comissão armazenada separadamente
- Para uma COMPRA: custo de aquisição = total_value + fee_amount
- Para uma VENDA: receita = total_value − fee_amount

Cada importador trata esta lógica especificamente para o formato da sua exchange.

---

## Fase 3 — Cálculo FIFO

```bash
python3 calculators/calculate_fifo.py
```

**O que faz:**
1. Toma **todas** as transações desde a primeira
2. Para cada COMPRA: cria um lote FIFO com a quantidade disponível
3. Para cada VENDA: associa ao lote mais antigo com quantidade restante
4. Calcula o período de detenção, custo de aquisição, mais/menos-valia
5. Guarda tudo em `fifo_lots` e `sale_lot_matches`

**Tempo**: ~2 minutos para dezenas de milhares de transações.

**Importante**: o FIFO recalcula sempre **tudo** (apaga fifo_lots e sale_lot_matches de cada cripto e recalcula). Não é possível calcular apenas um ano porque os lotes dependem da cadeia completa de compras.

---

## Fase 4 — Geração de relatórios

### Relatório IRS (para declaração fiscal)

```bash
python3 calculators/generate_irs_report.py YYYY
```

Gera: `data/reports/IRS_Crypto_FIFO_YYYY.xlsx` com 4 folhas:

| Folha | Conteúdo |
|-------|----------|
| Resumo | Resumo: total isento, total tributável, imposto estimado |
| Anexo G1 Quadro 07 | Vendas isentas (detenção ≥365 dias) — agregação diária por exchange |
| Anexo J Quadro 9.4A | Vendas tributáveis (<365 dias) em plataformas estrangeiras |
| Detalhe | Agregações diárias com número de operações subjacentes |

A agregação diária agrupa por data + exchange + estado fiscal (isento/tributável), conforme exigido pela AT. Vendas isentas e tributáveis **nunca se misturam**, mesmo que ocorram no mesmo dia na mesma exchange.

### Resumo anual (consola)

```bash
python3 reports/generate_annual_summary.py YYYY
```

Mostra: compras, vendas, resultado FIFO (isento/tributável), detalhe por exchange, participações restantes.

---

## Consultas de verificação

### Origem das vendas por ano de compra

```sql
SELECT 
    strftime('%Y', purchase_date) as ano_compra,
    COUNT(DISTINCT sale_transaction_id) as num_vendas,
    SUM(amount_sold) as btc_vendido,
    ROUND(AVG(holding_period_days) / 365.25, 1) as media_anos_detencao,
    SUM(CASE WHEN holding_period_days >= 365 THEN amount_sold ELSE 0 END) as btc_isento
FROM sale_lot_matches slm
JOIN transactions t ON slm.sale_transaction_id = t.id
WHERE strftime('%Y', slm.sale_date) = ?
AND t.cryptocurrency = 'BTC'
GROUP BY ano_compra
ORDER BY ano_compra;
```

### Cadeia de prova detalhada

```sql
SELECT 
    slm.sale_date as data_venda,
    slm.purchase_date as data_compra,
    slm.amount_sold as quantidade,
    slm.holding_period_days as dias_detencao,
    t_purchase.exchange_name as exchange_compra,
    t_sale.exchange_name as exchange_venda,
    slm.purchase_price_per_unit as preco_compra,
    slm.sale_price_per_unit as preco_venda,
    slm.gain_loss as mais_menos_valia
FROM sale_lot_matches slm
JOIN transactions t_sale ON slm.sale_transaction_id = t_sale.id
JOIN fifo_lots fl ON slm.fifo_lot_id = fl.id
JOIN transactions t_purchase ON fl.purchase_transaction_id = t_purchase.id
WHERE strftime('%Y', slm.sale_date) = ?
AND slm.holding_period_days >= 365
ORDER BY slm.sale_date, slm.purchase_date
LIMIT 100;
```

### Vendas não associadas (devem ser zero)

```sql
SELECT 
    t.transaction_date,
    t.exchange_name,
    t.amount as vendido,
    COALESCE(SUM(slm.amount_sold), 0) as associado,
    t.amount - COALESCE(SUM(slm.amount_sold), 0) as nao_associado
FROM transactions t
LEFT JOIN sale_lot_matches slm ON t.id = slm.sale_transaction_id
WHERE t.transaction_type = 'SELL'
AND t.cryptocurrency = 'BTC'
GROUP BY t.id
HAVING nao_associado > 0.00000001;
```

Se existirem vendas não associadas: faltam transações de compra, ou transferências BTC não foram registadas.

---

## Problemas comuns e soluções

### CSV com codificação não UTF-8

```bash
# Converter para UTF-8
iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv
# Ou de UTF-16 (típico do Wirex)
iconv -f UTF-16LE -t UTF-8 wirex.csv > wirex_utf8.csv
```

### Preços em falta em dados históricos

Se uma exchange histórica não tinha preços no CSV, usar a API CryptoCompare (plano gratuito, histórico completo desde a origem da moeda):

```python
import requests, time

CRYPTOCOMPARE_API_KEY = "YOUR_KEY"  # gratuito em cryptocompare.com

def get_historical_price(coin, currency, timestamp):
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {"fsym": coin, "tsym": currency, "limit": 1, "toTs": timestamp}
    headers = {"authorization": f"Apikey {CRYPTOCOMPARE_API_KEY}"}
    response = requests.get(url, headers=headers, params=params)
    return response.json()["Data"]["Data"][-1]["close"]

# Uso: passar um timestamp UNIX para a data necessária
from datetime import datetime
ts = int(datetime(2014, 5, 21).timestamp())
preco_eur = get_historical_price("BTC", "EUR", ts)
time.sleep(0.5)  # respeitar limites de taxa
```

### Duplicados

Os importadores usam DELETE + reimportar: apagam todos os dados da exchange e reimportam o CSV completo. Sem risco de duplicados.

---

## Boas práticas

1. **Manter os CSVs originais** — nunca apagar os ficheiros fonte
2. **Backup antes do FIFO** — `cp data/crypto_fifo.db data/backups/crypto_fifo.db.backup_$(date +%Y%m%d)`
3. **Verificar cada importação** — usar `verify_exchange_import.py`
4. **Atualizar `eurusd.csv`** — antes de importar exchanges em USD
5. **Nunca recriar a base de dados** — é a fonte de verdade permanente
6. **Guardar base de dados + relatórios por 7+ anos** — obrigação fiscal
