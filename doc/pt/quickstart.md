# Início Rápido — Crypto FIFO Tracker

Guia rápido para importar dados, calcular FIFO e gerar o relatório IRS.

---

## Pré-requisitos

- macOS/Linux com Python 3.11+
- Ambiente virtual ativado
- Ficheiros CSV descarregados das exchanges na pasta `data/`
- Ficheiro `data/eurusd.csv` atualizado com as taxas do BCE

```bash
cd ~/crypto-fifo-tracker
source venv/bin/activate
pip install pandas pytz openpyxl requests
```

---

## Fluxo de trabalho completo

### 1. Importar transações

Cada exchange tem um importador dedicado em `importers/`. Cada importador:
- Pergunta se deve apagar os dados existentes dessa exchange (opção DELETE)
- Lê o CSV da pasta `data/`
- Insere as transações na tabela `transactions`
- Para exchanges em USD (Coinbase Prime, Bitstamp, Bitfinex): converte para EUR via `ecb_rates.py`

```bash
# Importar uma exchange de cada vez
python3 importers/import_binance_with_fees.py
python3 importers/import_coinbase_prime.py
python3 importers/import_bitstamp_with_fees.py
python3 importers/import_bitfinex_ecb.py
python3 importers/import_kraken_with_fees.py
python3 importers/import_mtgox_with_fees.py
python3 importers/import_trt_with_fees.py
python3 importers/import_wirex.py
python3 importers/import_revolut.py
python3 importers/import_coinbase_standalone.py
python3 importers/import_binance_card.py

# Para transações genéricas em formato standard:
python3 importers/import_standard_csv.py data/ficheiro.csv "Nome Exchange"
```

**Estratégia de reimportação**: os importadores usam DELETE + reimportar. Todos os dados da exchange são eliminados e reimportados a partir do CSV completo. Sem risco de duplicados.

**Verificar** após cada importação:
```bash
python3 importers/verify_exchange_import.py "Binance"
python3 importers/verify_exchange_import.py "Coinbase Prime"
```

### 2. Calcular FIFO

O cálculo FIFO começa sempre **desde a primeira transação** e processa o histórico completo. Não é possível calcular apenas um ano.

```bash
python3 calculators/calculate_fifo.py
```

Isto preenche duas tabelas:
- `fifo_lots` — lotes de compra com quantidade restante
- `sale_lot_matches` — cada venda associada ao lote de compra mais antigo

Tempo estimado: ~2 minutos para dezenas de milhares de transações.

### 3. Gerar relatórios

**Relatório IRS** (Excel com folhas Anexo G1, Anexo J, Resumo):
```bash
python3 calculators/generate_irs_report.py YYYY
```
Gera: `data/reports/IRS_Crypto_FIFO_YYYY.xlsx`

**Resumo anual** (saída na consola com compras/vendas/FIFO/participações):
```bash
python3 reports/generate_annual_summary.py YYYY
```

---

## Adicionar dados de um novo ano

Quando chegam novas transações:

1. **Atualizar os CSVs** — adicionar as novas linhas aos ficheiros CSV existentes em `data/` (um ficheiro por exchange com todo o histórico, não ficheiros separados por ano)
2. **Atualizar taxas BCE** — descarregar `data/eurusd.csv` atualizado
3. **Reimportar as exchanges modificadas** — usando DELETE + reimportar
4. **Verificar** — `verify_exchange_import.py`
5. **Recalcular FIFO** — `calculate_fifo.py` (recalcula tudo)
6. **Gerar relatórios** — para cada ano necessário

```bash
# Exemplo: novos dados do Coinbase Prime
python3 importers/import_coinbase_prime.py     # opção 1 = DELETE + reimportar
python3 importers/verify_exchange_import.py "Coinbase Prime"
python3 calculators/calculate_fifo.py
python3 calculators/generate_irs_report.py YYYY
```

---

## Cópias de segurança

**Antes de operações críticas**, fazer backup da base de dados:

```bash
cp data/crypto_fifo.db data/backups/crypto_fifo.db.backup_$(date +%Y%m%d)
```

A base de dados `data/crypto_fifo.db` é a **fonte de verdade permanente**. Nunca deve ser recriada de raiz — apenas atualizada e recalculada.

---

## Consultas úteis

```bash
sqlite3 data/crypto_fifo.db
```

```sql
-- Transações por exchange
SELECT exchange_name, COUNT(*) as n, 
       SUM(CASE WHEN transaction_type='BUY' THEN 1 ELSE 0 END) as compras,
       SUM(CASE WHEN transaction_type='SELL' THEN 1 ELSE 0 END) as vendas
FROM transactions GROUP BY exchange_name;

-- Resumo anual de mais/menos-valias
SELECT strftime('%Y', sale_date) as ano, cryptocurrency,
       COUNT(*) as operacoes, SUM(gain_loss) as mais_menos_valias,
       SUM(CASE WHEN holding_period_days >= 365 THEN gain_loss ELSE 0 END) as isento,
       SUM(CASE WHEN holding_period_days < 365 THEN gain_loss ELSE 0 END) as tributavel
FROM sale_lot_matches GROUP BY ano, cryptocurrency;

-- Lotes FIFO abertos
SELECT cryptocurrency, COUNT(*) as lotes, SUM(remaining_amount) as quantidade
FROM fifo_lots WHERE remaining_amount > 0.00000001
GROUP BY cryptocurrency;
```
